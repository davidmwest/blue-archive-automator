"""Collect an event's side rewards: Task claims, card draws and shop exchanges.

Runs for seasons whose profile lists ``extras``. Task claims are free and are
always collected. Card draws and shop exchanges spend only that season's own
event currencies, one verified transaction at a time, and need the same opt-in
as Treasure Hunt (``[ap].event_treasure_enabled``).
"""
from datetime import datetime, timedelta

from .ap_vision import APVision
from .event_extras_vision import (ExtrasVision, PAGE_BUTTONS, SHOP_TABS, normalized, same_item)
from .event_inspection import EventVision, enter_event
from .locking import InstanceLock
from .shop_runtime import ShopRunner

EVENT_PAGES = {'event_page', 'event_list'}
VISIT_SECONDS, VISIT_INPUTS = 3600, 700
# Yield between transactions while a whole one still fits in the visit.
YIELD_SECONDS, YIELD_INPUTS = 2700, 540
FINAL_HOURS = 36


def shows_balance(screen, expected):
    """The page shows the expected balance; a single digit may go unread."""
    return screen.balance == expected or (screen.balance is None and 0 <= expected < 10)


def purchase_plan(items, balance, priority=(), skip=(), *, greedy=False):
    """Order one shop tab's limited stock; return ``[(item, quantity), ...]``.

    Items are bought strictly in priority order: while a wanted item is still
    in stock and cannot be cleared, nothing cheaper below it is bought, so the
    currency keeps accumulating for it. ``greedy`` drops that rule for the last
    hours of a season, when saving has no further use. Unlimited conversions
    and unreadable stock are never bought.
    """
    priority = [normalized(entry) for entry in priority]
    skip = [normalized(entry) for entry in skip]

    def rank(indexed):
        index, item = indexed
        name = normalized(item.name)
        tier = next((tier for tier, entry in enumerate(priority) if entry in name), len(priority))
        return tier, index

    wanted = [item for item in items
              if item.stock and item.price > 0 and not any(entry in normalized(item.name) for entry in skip)]
    plan, remaining = [], balance
    for _, item in sorted(enumerate(wanted), key=rank):
        quantity = min(item.stock, remaining // item.price)
        if quantity:
            plan.append((item, quantity))
            remaining -= quantity * item.price
        if quantity < item.stock and not greedy:
            break
    return plan


class EventExtrasRunner(ShopRunner):
    task = 'event_treasure'

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault('vision', ExtrasVision(startup, EventVision(startup, APVision(startup))))
        super().__init__(config, device, startup, **kwargs)
        self.deferred = False

    def budget(self):
        if self.clock() - self.started > VISIT_SECONDS or self.actions >= VISIT_INPUTS:
            self.fail('Event rewards visit reached its time or input limit')

    def out_of_time(self):
        if self.clock() - self.started < YIELD_SECONDS and self.actions < YIELD_INPUTS:
            return False
        if not self.deferred:
            self.deferred = True
            self.important('event_rewards_deferred',
                           'Event rewards visit yielded between transactions; the rest continues next visit')
        return True

    def important(self, action, detail, **fields):
        from .actions import record_action
        record_action(self.config, action, detail, task=self.task, **fields)
        self.phase(detail)

    def back_to_event(self, frame):
        self.tap(frame, (55, 25), 'Return to the event page')
        return self.wait(EVENT_PAGES)

    def collect(self, receipt, name, label, *, itemize=True):
        """Record one Reward Acquired overlay and close it.

        A drawn card's receipt lists about 18 random rewards over several
        pages. Reading every tooltip took over seven minutes per draw live, so
        those receipts are kept as images instead of itemized.
        """
        from .event_quests import itemize_or_keep
        evidence = self.run_dir / name
        self.journal.save_image(evidence.name, receipt.capture.png)
        if itemize:
            receipt = itemize_or_keep(self, receipt, evidence, label)
        self.tap(receipt, receipt.screen.target, f'Close {label} rewards')
        return evidence

    # Tasks -----------------------------------------------------------------

    def claim_tasks(self, frame):
        """Event Tasks are free; Claim All is tapped only while it is lit."""
        self.tap(frame, PAGE_BUTTONS['tasks'], 'Open event Tasks')
        frame = self.wait('event_tasks')
        claimed = 0
        for _ in range(3):
            if frame.screen.target is None:
                break
            self.tap(frame, frame.screen.target, 'Claim all completed event Tasks')
            self.collect(self.wait('receipt', timeout=60), f'event-tasks-{claimed + 1}.png', 'Event Task')
            claimed += 1
            frame = self.wait('event_tasks')
        self.important('event_tasks_checked',
                       'Event Tasks: claimed completed rewards' if claimed
                       else 'Event Tasks: nothing to claim')
        return self.back_to_event(frame)

    # Draw a Card -------------------------------------------------------------

    def settled_cards(self, predicate=lambda screen: True):
        """Two matching reads; a flip animation must not be mistaken for a card."""
        previous = self.wait('event_cards', predicate=predicate)
        for _ in range(4):
            self.sleep(1)
            current = self.wait('event_cards', predicate=predicate)
            if previous.screen == current.screen:
                return current
            previous = current
        self.fail('Draw a Card is still changing; no event currency spent')

    def draw_cards(self, frame, settings):
        cost = settings['cost']
        self.tap(frame, PAGE_BUTTONS['cards'], 'Open Draw a Card')
        frame = self.settled_cards()
        draws = shuffles = 0
        for _ in range(4 * settings.get('visit_limit', 40)):
            screen = frame.screen
            # An unreadable balance is a lone small number; nothing is drawn on it.
            if (screen.balance is None or screen.balance < cost
                    or draws >= settings.get('visit_limit', 40) or self.out_of_time()):
                break
            if not screen.face_down or screen.premium:
                # A set holds one high-grade card. Once it is out, or the set is
                # used up, the free shuffle deals a new set of four.
                self.tap(frame, screen.shuffle, 'Shuffle to a new card set (free)')
                frame = self.settled_cards(lambda s: len(s.face_down) == 4 and s.balance == screen.balance)
                shuffles += 1
                continue
            if screen.draw_one is None:
                self.fail('Draw a Card is unavailable despite enough event currency; none spent')
            self.tap(frame, screen.draw_one, f'Draw one event card for {cost} event currency')
            evidence = self.collect(self.wait('receipt', timeout=60), f'event-card-{draws + 1:03d}.png',
                                    'Event card', itemize=False)
            frame = self.settled_cards(lambda s: shows_balance(s, screen.balance - cost))
            draws += 1
            if len(frame.screen.face_down) != len(screen.face_down) - 1:
                self.journal.record('event_card_count_unexpected', before=screen.face_down,
                                    after=frame.screen.face_down)
            self.important('event_card_drawn',
                           f'Drew an event card: {cost} event currency spent; {frame.screen.balance} left',
                           currency_spent=cost, currency_remaining=frame.screen.balance,
                           evidence=str(evidence))
        left = 'an unread small amount of' if frame.screen.balance is None else frame.screen.balance
        self.important('event_cards_checked',
                       f'Draw a Card: {draws} drawn, {shuffles} free shuffles; {left} event currency left',
                       draws=draws, currency_remaining=frame.screen.balance)
        return self.back_to_event(frame)

    # Event Shop --------------------------------------------------------------

    def shop_frame(self, tab, predicate=lambda screen: True):
        return self.wait('event_shop', predicate=lambda s: s.tab == tab and predicate(s))

    def scroll_shop(self, frame, direction):
        points = ((935, 560), (935, 330)) if direction == 'down' else ((935, 330), (935, 560))
        if (not frame.capture.is_fresh(self.clock())
                or self.device.foreground_package() != self.config.package):
            self.fail('Event shop changed before scrolling')
        self.journal.record('intent', operation='swipe', points=points,
                            detail=f'Scroll the event shop {direction}')
        if not self.device.swipe(*points, duration_ms=700, deadline=frame.capture.deadline,
                                 monotonic=self.clock):
            self.fail('Event shop scroll expired')
        self.actions += 1
        self.sleep(1.2)

    def walk_shop(self, tab, direction, visit=lambda frame: None):
        """Scroll one way until the rows stop changing; ``visit`` may stop early."""
        last, stationary = None, 0
        for _ in range(16):
            frame = self.shop_frame(tab)
            found = visit(frame)
            if found is not None:
                return frame, found
            visible = tuple(item.key for item in frame.screen.items)
            stationary = stationary + 1 if visible == last else 0
            if stationary >= 2:
                return frame, None
            last = visible
            self.scroll_shop(frame, direction)
        self.fail('Event shop did not stop scrolling; no event currency spent')

    def shop_catalog(self, tab):
        self.walk_shop(tab, 'up')
        catalog = {}

        def gather(frame):
            for item in frame.screen.items:
                catalog[item.key] = item
        self.walk_shop(tab, 'down', gather)
        return list(catalog.values())

    def find_item(self, tab, wanted):
        def visible(frame):
            return next((item for item in frame.screen.items if item.key == wanted.key), None)
        for direction in ('down', 'up'):
            frame, item = self.walk_shop(tab, direction, visible)
            if item is not None:
                return frame, item
        return frame, None

    def purchase(self, tab, frame, item, quantity, balance):
        self.tap(frame, item.target, f'Review {item.name} in the event shop')
        dialog = self.wait('event_purchase', predicate=lambda s: s.target is not None)
        if quantity > 1:
            self.tap(dialog, dialog.screen.maximum, f'Select the largest affordable quantity of {item.name}')
            dialog = self.wait('event_purchase', predicate=lambda s: s.target is not None and s.quantity > 1)
        offer = dialog.screen
        if (not same_item(offer.name, item.name) or offer.unit_cost != item.price
                or not 1 <= offer.quantity <= item.stock or offer.total_cost > balance):
            self.tap(dialog, (515, 520), 'Cancel an event purchase that does not match its card')
            self.fail(f'Event shop offer for {item.name} did not match its card; nothing was bought')
        self.journal.save_image(f'event-shop-{tab + 1}-{normalized(item.name)[:40]}-{item.price}-offer.png', dialog.capture.png)
        self.important('event_purchase_requested',
                       f'Event shop: {offer.quantity}× {item.name} for {offer.total_cost} event currency',
                       item=item.name, quantity=offer.quantity, currency_cost=offer.total_cost)
        self.tap(dialog, offer.target, f'Buy {offer.quantity}× {item.name} with event currency')
        evidence = self.collect(self.wait('receipt', timeout=60),
                                f'event-shop-{tab + 1}-{normalized(item.name)[:40]}-{item.price}.png', 'Event shop')
        left = balance - offer.total_cost
        frame = self.shop_frame(tab, lambda s: shows_balance(s, left))
        self.important('event_item_purchased',
                       f'Event shop: bought {offer.quantity}× {item.name}; {left} event currency left',
                       item=item.name, quantity=offer.quantity, currency_spent=offer.total_cost,
                       currency_remaining=left, evidence=str(evidence))
        return frame, left

    def tab_balance(self, tab):
        """A readable balance, or None after two reads (a lone small number)."""
        frame = self.shop_frame(tab)
        if frame.screen.balance is None:
            self.sleep(1)
            frame = self.shop_frame(tab)
        return frame, frame.screen.balance

    def shop(self, frame, settings, greedy):
        self.tap(frame, PAGE_BUTTONS['shop'], 'Open the event shop')
        frame = self.wait('event_shop')
        bought = 0
        for tab, rules in enumerate(settings['tabs'][:len(SHOP_TABS)]):
            if self.out_of_time():
                break
            if frame.screen.tab != tab:
                self.tap(frame, SHOP_TABS[tab], f'Open event shop tab {tab + 1}')
            frame, balance = self.tab_balance(tab)
            if not balance:
                self.journal.record('event_shop_tab_skipped', tab=tab + 1, balance=balance)
                continue
            plan = purchase_plan(self.shop_catalog(tab), balance, rules.get('priority', ()),
                                 rules.get('skip', ()), greedy=greedy)
            self.journal.record('event_shop_plan', tab=tab + 1, balance=balance,
                                plan=[(item.name, item.price, quantity) for item, quantity in plan])
            for wanted, quantity in plan:
                if self.out_of_time():
                    break
                frame, item = self.find_item(tab, wanted)
                if item is None or not item.stock:
                    self.journal.record('event_shop_item_missing', tab=tab + 1, item=wanted.name)
                    continue
                quantity = min(quantity, item.stock, balance // item.price)
                if quantity:
                    frame, balance = self.purchase(tab, frame, item, quantity, balance)
                    bought += 1
            frame = self.shop_frame(tab)
        self.important('event_shop_checked', f'Event shop: {bought} purchases this visit', purchases=bought)
        return self.back_to_event(frame)

    def run(self, profile):
        extras = profile.get('extras', {})
        frame = enter_event(self, self.startup)
        if extras.get('tasks'):
            frame = self.claim_tasks(frame)
        if self.config.ap_event_treasure_enabled:
            if extras.get('cards'):
                frame = self.draw_cards(frame, extras['cards'])
            if extras.get('shop') and not self.out_of_time():
                ends = datetime.fromisoformat(profile['playable_until'])
                greedy = ends - self.wall_clock() <= timedelta(hours=FINAL_HOURS)
                frame = self.shop(frame, extras['shop'], greedy)
        else:
            self.phase('Automatic event currency spending is off; only free Task rewards were checked')
        self.tap(frame, (1237, 24), 'Return home after event rewards')
        self.home()
        return self.finish()


def run_event_extras(config, device, startup, profile, **kwargs):
    with InstanceLock(config):
        runner = EventExtrasRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run(profile)
        except Exception as exc:
            runner.journal.record('finished', status='failed', detail=str(exc))
            raise
        finally:
            runner.journal.close()
