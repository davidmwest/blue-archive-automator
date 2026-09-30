"""Opt-in purchases of explicitly named, price-limited paid packs."""
from .actions import record_action
from .locking import InstanceLock
from .packs_state import PACKS, PERMANENT_PACKS, WEEKLY_PACKS, enabled, next_reset, read_state, write_state
from .shop_runtime import ShopRunner


class PacksRunner(ShopRunner):
    task = 'packs'
    allows_billing = True

    def __init__(self, *args, allow_retry=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_retry = allow_retry

    def settle_store(self):
        expected = {self.selected_pack} if getattr(self, 'selected_pack', None) else set(PERMANENT_PACKS)
        previous = self.wait('store', predicate=lambda s: expected <= set(s.cards))
        for _ in range(5):
            self.sleep(1)
            current = self.wait('store', predicate=lambda s: expected <= set(s.cards))
            if previous.screen.cards == current.screen.cards:
                return current
            previous = current
        self.fail('Pack availability did not settle; purchases stopped')

    def scroll_store(self, frame, *, down):
        if not frame.capture.is_fresh(self.clock()) or self.device.foreground_package() != self.config.package:
            self.fail('Store changed before scrolling; no input sent')
        start, end = ((700, 605), (700, 280)) if down else ((700, 280), (700, 605))
        self.journal.record('intent', operation='swipe', detail='Browse named paid packs', down=down)
        if not self.device.swipe(start, end, 800, deadline=frame.capture.deadline, monotonic=self.clock):
            self.fail('Store scroll expired')
        self.actions += 1
        self.sleep(2)

    def select_pack(self, key):
        self.selected_pack = key if key in WEEKLY_PACKS else None
        frame = self.wait('store')
        if key not in WEEKLY_PACKS and set(PERMANENT_PACKS) <= set(frame.screen.cards):
            return self.settle_store()
        tab = (380, 180) if key == 'weekly_ap_iv' else (900, 180) if key in WEEKLY_PACKS else (640, 180)
        self.tap(frame, tab, f'Open store tab for {PACKS[key][0]}')
        frame = self.wait('store')
        if key not in WEEKLY_PACKS or key in frame.screen.cards:
            return self.settle_store()
        # Reset the scroll position, then search a bounded number of pages.
        for _ in range(6):
            self.scroll_store(frame, down=False)
            frame = self.wait('store')
            if key in frame.screen.cards:
                return self.settle_store()
        for page in range(10):
            if key in frame.screen.cards:
                return self.settle_store()
            if page < 9:
                self.scroll_store(frame, down=True)
                frame = self.wait('store')
        self.phase(f'{PACKS[key][0]} is not visible in the current store; skipping')
        return None

    def purchase(self, frame, key):
        card = frame.screen.cards[key]
        maximum = getattr(self.config, f'packs_{key}_max_cents')
        if key not in enabled(self.config):
            self.fail('This pack is not enabled for paid purchases')
        if card['state'] != 'available' or not 0 < card['cents'] <= maximum:
            self.fail('Pack availability or USD price does not match its spending limit')
        self.tap(frame, card['target'], f'Inspect {PACKS[key][0]}')
        confirmation = self.wait('purchase_confirm')
        if confirmation.screen.pack != key or confirmation.screen.cents != card['cents']:
            self.fail('In-game purchase details changed; purchase stopped')
        self.tap(confirmation, confirmation.screen.target, 'Open Google Play checkout')
        checkout = self.wait('checkout', timeout=60)
        if checkout.screen.pack != key or checkout.screen.cents != card['cents']:
            self.fail('Google Play product or price differs from the approved game product')
        # Persist BEFORE the charge. Any interruption after this point requires
        # observable delivery/active status before another purchase is possible.
        self.state['pending'] = {'pack': key, 'cents': card['cents'], 'time': self.wall_clock().isoformat()}
        write_state(self.config, self.state)
        title, cents = PACKS[key][0], card['cents']
        record_action(self.config, 'pack_purchase_requested', f'Buying {title}: USD {cents / 100:.2f}',
                      task='packs', pack=key, currency='USD', price_cents=cents)
        self.tap(checkout, checkout.screen.target, f'Buy {title} for USD {cents / 100:.2f}')
        # Play briefly shows a processing screen after submission. Observe only:
        # the persisted intent prevents a second charge if delivery stays unclear.
        outcome = self.wait({'delivered', 'backup_prompt'}, timeout=90, billing_grace=10)
        if outcome.screen.kind == 'backup_prompt':
            self.tap(outcome, outcome.screen.target, 'Dismiss optional backup payment setup')
            outcome = self.wait('delivered', timeout=60)
        self.journal.save_image(f'{key}-delivered.png', outcome.capture.png)
        record_action(self.config, 'pack_purchased', f'Bought {title}: USD {cents / 100:.2f}; delivered to mail',
                      task='packs', pack=key, currency='USD', price_cents=cents)
        self.state['pending'] = None
        write_state(self.config, self.state)
        self.tap(outcome, outcome.screen.target, 'Acknowledge confirmed delivery')
        # Returning from Play can reset both the selected tab and scroll position.
        frame = self.select_pack(key)
        if frame is None:
            self.fail('Delivery confirmed but purchased pack is no longer visible')
        if frame.screen.cards[key]['state'] not in ({'mail', 'active', 'unavailable'} if key == 'weekly_reports_lite' else {'mail', 'active'}):
            self.state['pending'] = {'pack': key, 'cents': cents, 'time': self.wall_clock().isoformat()}
            write_state(self.config, self.state)
            self.fail('Delivery notice appeared but pack ownership is unclear; automatic purchases blocked')
        return frame

    def run(self):
        self.state = read_state(self.config)
        if not self.allow_retry and (self.state['blocked_reason'] or self.state['pending']):
            self.fail('Paid pack job is disabled after a failure or unresolved charge; review it and explicitly retry the pack check')
        self.navigate('home', 'store', (139, 263))
        frame = self.select_pack('monthly')
        pending = self.state['pending']
        if pending:
            frame = self.select_pack(pending['pack'])
            state = frame.screen.cards[pending['pack']]['state'] if frame else 'unknown'
            # A sold-out consumable alone does not prove an interrupted payment
            # succeeded. Require explicit delivery evidence or manual resolution.
            if state not in {'active', 'mail'}:
                self.fail('An earlier charge is unresolved. Check Google Play purchase history; no second charge will be attempted')
            record_action(self.config, 'pack_purchase_reconciled',
                          f'{PACKS[pending["pack"]][0]} is now {state}; prior attempt will not be repeated', task='packs')
            self.state['pending'] = None
        write_state(self.config, self.state)
        for key in PACKS:
            if key in WEEKLY_PACKS and key not in enabled(self.config):
                continue
            frame = self.select_pack(key)
            if frame is None:
                continue
            card = frame.screen.cards[key]
            self.state['observed'][key] = {k: card[k] for k in ('state', 'days', 'cents')}
            write_state(self.config, self.state)
            if key in enabled(self.config) and card['state'] == 'available':
                frame = self.purchase(frame, key)
                card = frame.screen.cards[key]
                self.state['observed'][key] = {k: card[k] for k in ('state', 'days', 'cents')}
            elif key in enabled(self.config) and card['state'] == 'unknown':
                self.fail(f'{PACKS[key][0]} status is unreadable; purchases stopped')
            suffix = f' ({card["days"]} days remaining)' if card['days'] is not None else ''
            self.phase(f'{PACKS[key][0]}: {card["state"]}{suffix}')
        self.state['next_check_at'] = next_reset(self.wall_clock())
        self.state['blocked_reason'] = None
        write_state(self.config, self.state)
        frame = self.wait('store')
        self.tap(frame, (1009, 113), 'Close pack store')
        self.home()
        self.phase('Pack check complete; collect mail next')
        return self.finish()


def run_packs(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = PacksRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except Exception as exc:
            if hasattr(runner, 'state'):
                runner.state['blocked_reason'] = str(exc)
                write_state(config, runner.state)
            record_action(config, 'packs_blocked', str(exc), task='packs')
            runner.journal.record('finished', status='failed', detail=str(exc))
            raise
        finally:
            runner.journal.close()
