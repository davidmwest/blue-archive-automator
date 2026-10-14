"""Event Task claims, card draws and shop exchanges: recognition, policy and flow."""

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ba_automator.event_extras import EventExtrasRunner, purchase_plan
from ba_automator.event_extras_vision import (
    CARD_SLOTS, PAGE_BUTTONS, SHOP_TABS, ExtrasScreen, ShopItem, classify_extras,
    premium_border, same_item)
from ba_automator.event_priority import available_event
from ba_automator.vision import Word

FIXTURES = Path(__file__).parent / 'fixtures/event_extras'
YELLOW, CYAN = (40, 200, 250), (250, 200, 90)


def words(name):
    return [Word(w['text'], w['confidence'], tuple(w['box']))
            for w in json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))]


def page(*, tab=None):
    """The fixtures hold OCR text only; recognizers also check a few colours."""
    frame = np.full((720, 1280, 3), 70, np.uint8)
    frame[5:30, 300:460] = 240          # the undimmed top bar
    for index, (x, y) in enumerate(SHOP_TABS):
        frame[y - 22:y + 22, x - 60:x - 30] = 60 if index == tab else 240
    return frame


def profile():
    return available_event(datetime(2026, 10, 14, tzinfo=timezone.utc))


# Recognition ----------------------------------------------------------------

def test_claim_all_is_a_target_only_while_lit():
    frame = page()
    assert classify_extras(frame, words('tasks-claimable')).target is None
    frame[650:690, 1075:1225] = YELLOW
    screen = classify_extras(frame, words('tasks-claimable'))
    assert (screen.kind, screen.target, screen.ap) == ('event_tasks', (1150, 670), 500)
    dimmed = frame.copy()
    dimmed[5:30, 300:460] = 70          # a reward overlay covers the page
    assert classify_extras(dimmed, words('tasks-claimable')) is None


def test_card_page_reads_balance_and_face_down_cards():
    frame = page()
    frame[560:605, 740:920] = CYAN
    fresh = classify_extras(frame, words('cards-fresh'))
    assert (fresh.kind, fresh.balance, fresh.face_down) == ('event_cards', 274, (0, 1, 2, 3))
    assert fresh.draw_one == (828, 582) and fresh.premium == ()
    drawn = classify_extras(page(), words('cards-one-drawn'))
    assert (drawn.balance, drawn.face_down, drawn.draw_one) == (74, (1, 2, 3), None)
    assert drawn.shuffle == (1150, 186)


def test_only_a_strongly_coloured_frame_that_is_not_blue_counts_as_high_grade():
    frame = np.full((720, 1280, 3), 245, np.uint8)       # a Normal card's pale frame
    slot = CARD_SLOTS[0]
    assert not premium_border(frame, slot)
    x1, y1, x2, y2 = slot
    frame[y1:y2, x1:x2] = (30, 190, 250)                 # gold
    assert premium_border(frame, slot)
    frame[y1:y2, x1:x2] = (250, 150, 40)                 # a Rare card's blue
    assert not premium_border(frame, slot)
    drawn = page()
    drawn[y1:y2, x1:x2] = (30, 190, 250)
    assert classify_extras(drawn, words('cards-one-drawn')).premium == (0,)


def test_shop_page_lists_fully_visible_cards_in_reading_order():
    screen = classify_extras(page(tab=0), words('shop'))
    assert (screen.kind, screen.tab, screen.balance) == ('event_shop', 0, 94)
    assert [(item.name, item.price, item.stock) for item in screen.items] == [
        ('Novice Activity Report', 1, 90), ('Normal Activity Report', 4, 69),
        ('Advanced Activity Report', 8, 45), ('Superior Activity Report', 40, 18),
        ('Beginner Tactical Training Blu-ray (Shanhaijing)', 10, 30),
        ('Normal Tactical Training Blu-ray (Shanhaijing)', 30, 18),
        ('Advanced Tactical Training Blu-ray (Shanhaijing)', 100, 12),
        ('Superior Tactical Training Blu-ray (Shanhaijing)', 300, 4)]
    assert screen.items[1].target == (859, 347)
    assert classify_extras(page(tab=2), words('shop')).tab == 2
    assert classify_extras(page(), words('shop')).tab is None    # no tab is marked


@pytest.mark.parametrize('name, quantity, total', [
    ('purchase-one', 1, 4), ('purchase-max', 24, 96), ('purchase-min', 1, 4)])
def test_purchase_notice_exposes_confirm_only_with_consistent_costs(name, quantity, total):
    frame = page()
    frame[5:30, 300:460] = 70
    frame[500:540, 690:850] = YELLOW
    screen = classify_extras(frame, words(name))
    assert (screen.kind, screen.name, screen.quantity, screen.unit_cost, screen.total_cost) == (
        'event_purchase', 'Normal Activity Report', quantity, 4, total)
    assert screen.target == (765, 520) and screen.maximum == (883, 350)
    wrong = [Word('5', w.confidence, w.box) if w.text == str(total) and w.center[0] > 790 else w
             for w in words(name)]
    unverified = classify_extras(frame, wrong)
    assert (unverified.kind, unverified.target) == ('event_purchase', None)


def test_item_names_must_agree_between_card_and_notice():
    assert same_item('Normal Activity Report', 'Normal  Activity Report')
    assert same_item('Beginner Tactical Training Blu-ray (Shanhaijing)', 'Beginner Tactical Training Blu-ray')
    assert not same_item('Normal Activity Report', 'Novice Activity Report')
    assert not same_item('Credits', 'Cred')
    assert not same_item('', 'Normal Activity Report')


# Policy -----------------------------------------------------------------------

def item(name, price, stock):
    return ShopItem(name, price, stock, (0, 0))


CATALOG = [item('Novice Activity Report', 1, 90), item('Superior Blu-ray', 300, 4),
           item('Credits', 5, None), item('Mika Eleph', 60, 5)]


def planned(*args, **kwargs):
    return [(entry.name, quantity) for entry, quantity in purchase_plan(*args, **kwargs)]


def test_plan_follows_priority_and_saves_for_an_unfinished_item():
    assert planned(CATALOG, 500, ['eleph', 'blu-ray']) == [('Mika Eleph', 5)]
    assert planned(CATALOG, 1000, ['eleph', 'blu-ray']) == [('Mika Eleph', 5), ('Superior Blu-ray', 2)]
    assert planned(CATALOG, 1600, ['eleph', 'blu-ray']) == [
        ('Mika Eleph', 5), ('Superior Blu-ray', 4), ('Novice Activity Report', 90)]
    assert planned(CATALOG, 59, ['eleph']) == []


def test_unlisted_items_follow_in_shop_order_and_unlimited_stock_is_never_bought():
    assert planned(CATALOG, 5000) == [
        ('Novice Activity Report', 90), ('Superior Blu-ray', 4), ('Mika Eleph', 5)]
    assert planned(CATALOG, 5000, skip=['activity report']) == [('Superior Blu-ray', 4), ('Mika Eleph', 5)]
    assert planned([item('Credits', 5, None), item('Sold out', 5, 0)], 5000) == []


def test_last_hours_spend_whatever_is_affordable():
    assert planned(CATALOG, 250, ['blu-ray', 'eleph']) == []
    assert planned(CATALOG, 250, ['blu-ray', 'eleph'], greedy=True) == [
        ('Mika Eleph', 4), ('Novice Activity Report', 10)]


# Flow -------------------------------------------------------------------------

class Game(EventExtrasRunner):
    """A small model of the event's side pages, driven through the real runner."""

    def __init__(self, *, claims=0, cards=0, grades=(), shops=(), balances=(), spending=True):
        self.config = SimpleNamespace(package='game', ap_event_treasure_enabled=spending)
        self.device = SimpleNamespace(foreground_package=lambda: 'game', swipe=self.swipe)
        self.journal = SimpleNamespace(record=lambda *a, **k: None, save_image=lambda *a: None)
        self.startup, self.run_dir = None, Path('run')
        self.time, self.started, self.actions, self.deferred = 0.0, 0.0, 0, False
        self.where, self.log, self.notes = 'event_page', [], []
        self.claims, self.card_balance, self.grades = claims, cards, list(grades)
        self.revealed, self.shops, self.balances = [], [list(tab) for tab in shops], list(balances)
        self.tab, self.row, self.offer, self.behind = 0, 0, None, None

    def clock(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def wall_clock(self):
        return datetime(2026, 10, 14, tzinfo=timezone.utc)

    def important(self, action, detail, **fields):
        self.notes.append(action)

    def home(self):
        assert self.where == 'home'

    def screen(self):
        if self.where == 'event_tasks':
            return ExtrasScreen('event_tasks', target=(1150, 670) if self.claims else None)
        if self.where == 'event_cards':
            affordable = self.card_balance >= 200
            return ExtrasScreen(
                'event_cards', balance=self.card_balance, shuffle=(1150, 186),
                draw_one=(828, 582) if affordable else None,
                face_down=tuple(range(len(self.revealed), 4)),
                premium=tuple(i for i, grade in enumerate(self.revealed) if grade == 'gold'))
        if self.where == 'event_shop':
            rows = self.shops[self.tab][self.row * 4:self.row * 4 + 8]
            items = tuple(ShopItem(entry.name, entry.price, entry.stock,
                                   (708 + 151 * (index % 4), 347 + 253 * (index // 4)))
                          for index, entry in enumerate(rows) if entry.stock != 0)
            return ExtrasScreen('event_shop', tab=self.tab, balance=self.balances[self.tab], items=items)
        if self.where == 'event_purchase':
            entry, quantity = self.offer
            return ExtrasScreen('event_purchase', target=(765, 520), maximum=(883, 350), name=entry.name,
                                quantity=quantity, unit_cost=entry.price, total_cost=quantity * entry.price)
        return SimpleNamespace(kind=self.where, target=(640, 631), event=None)

    def wait(self, kinds, *, timeout=40, predicate=lambda screen: True):
        kinds = {kinds} if isinstance(kinds, str) else kinds
        screen = self.screen()
        assert screen.kind in kinds and predicate(screen), (kinds, screen)
        capture = SimpleNamespace(png=b'', deadline=self.time + 5, is_fresh=lambda now: True)
        return SimpleNamespace(screen=screen, capture=capture)

    def collect(self, receipt, name, label, *, itemize=True):
        assert self.where == 'receipt'
        self.where = self.behind
        self.itemized = getattr(self, 'itemized', []) + [(label, itemize)]
        return Path(name)

    def swipe(self, start, end, **kwargs):
        last = max(0, (len(self.shops[self.tab]) - 1) // 4 - 1)
        self.row = min(last, self.row + 1) if end[1] < start[1] else max(0, self.row - 1)
        return True

    def tap(self, frame, target, detail):
        self.actions += 1
        self.log.append(detail)
        if self.where == 'event_page':
            pages = {PAGE_BUTTONS['tasks']: 'event_tasks', PAGE_BUTTONS['cards']: 'event_cards',
                     PAGE_BUTTONS['shop']: 'event_shop', (1237, 24): 'home'}
            self.where = pages[target]
        elif target == (55, 25):
            self.where = 'event_page'
        elif self.where == 'event_tasks':
            assert target == (1150, 670) and self.claims
            self.claims, self.where, self.behind = 0, 'receipt', 'event_tasks'
        elif self.where == 'event_cards' and target == (1150, 186):
            assert self.revealed, 'a fresh set cannot be shuffled'
            self.revealed = []
        elif self.where == 'event_cards':
            assert target == (828, 582) and self.card_balance >= 200 and len(self.revealed) < 4
            self.card_balance -= 200
            self.revealed.append(self.grades.pop(0) if self.grades else 'normal')
            self.where, self.behind = 'receipt', 'event_cards'
        elif self.where == 'event_shop' and target in SHOP_TABS:
            self.tab, self.row = SHOP_TABS.index(target), 0
        elif self.where == 'event_shop':
            entry = next(entry for entry in frame.screen.items if entry.target == target)
            self.offer = (next(e for e in self.shops[self.tab] if e.name == entry.name), 1)
            self.where = 'event_purchase'
        elif self.where == 'event_purchase' and target == (883, 350):
            entry = self.offer[0]
            self.offer = (entry, min(entry.stock or 99, self.balances[self.tab] // entry.price))
        elif self.where == 'event_purchase' and target == (765, 520):
            entry, quantity = self.offer
            self.balances[self.tab] -= quantity * entry.price
            stock = self.shops[self.tab]
            stock[stock.index(entry)] = ShopItem(entry.name, entry.price,
                                                 entry.stock and entry.stock - quantity, entry.target)
            self.bought = getattr(self, 'bought', []) + [(entry.name, quantity)]
            self.where, self.behind = 'receipt', 'event_shop'
        else:
            raise AssertionError((self.where, target, detail))


@pytest.fixture
def entered(monkeypatch):
    monkeypatch.setattr('ba_automator.event_extras.enter_event', lambda runner, startup: runner.wait('event_page'))


def run(game, extras):
    game.finish = lambda: 'done'
    assert game.run({**profile(), 'extras': extras}) == 'done'
    return game


def test_tasks_are_claimed_once_and_spending_needs_the_opt_in(entered):
    game = run(Game(claims=3, cards=1000, spending=False), {'tasks': True, 'cards': {'cost': 200}})
    assert game.claims == 0 and game.card_balance == 1000
    assert game.notes == ['event_tasks_checked']
    quiet = run(Game(spending=False), {'tasks': True})
    assert quiet.log == ['Open event Tasks', 'Return to the event page', 'Return home after event rewards']


def test_cards_are_drawn_while_affordable_and_a_used_set_is_shuffled(entered):
    game = run(Game(cards=1150), {'cards': {'cost': 200}})
    assert game.card_balance == 150 and game.notes.count('event_card_drawn') == 5
    assert [detail for detail in game.log if 'Shuffle' in detail] == ['Shuffle to a new card set (free)']
    # Each draw's long random reward list is kept as an image, not itemized.
    assert set(game.itemized) == {('Event card', False)}


def test_a_high_grade_card_ends_its_set(entered):
    game = run(Game(cards=800, grades=['normal', 'gold', 'normal', 'normal']), {'cards': {'cost': 200}})
    order = [detail.split()[0] for detail in game.log if detail.startswith(('Draw', 'Shuffle'))]
    assert order == ['Draw', 'Draw', 'Shuffle', 'Draw', 'Draw']
    assert game.card_balance == 0 and len(game.revealed) == 2


def test_card_draws_stop_at_the_visit_limit(entered):
    game = run(Game(cards=2000), {'cards': {'cost': 200, 'visit_limit': 3}})
    assert game.card_balance == 1400


SHOP = [item('Novice Activity Report', 1, 90), item('Normal Activity Report', 4, 69),
        item('Advanced Activity Report', 8, 45), item('Superior Activity Report', 40, 18),
        item('Beginner Blu-ray', 10, 30), item('Normal Blu-ray', 30, 18),
        item('Advanced Blu-ray', 100, 12), item('Superior Blu-ray', 300, 4),
        item('Aether Dust', 2, 50), item('Aether Piece', 5, 30),
        item('Credits', 5, None), item('Mika Eleph', 60, 5),
        item('Furniture', 500, 1)]


def test_shop_buys_by_priority_across_scrolled_rows_and_tabs(entered):
    rules = {'tabs': [{'priority': ['eleph', 'furniture', 'superior blu-ray']},
                      {'skip': ['activity report']}]}
    game = run(Game(shops=[SHOP, SHOP[:5]], balances=[1500, 120]), {'shop': rules})
    assert game.bought == [('Mika Eleph', 5), ('Furniture', 1), ('Superior Blu-ray', 2),
                           ('Beginner Blu-ray', 12)]
    assert game.balances == [100, 0]
    assert game.notes.count('event_item_purchased') == 4


def test_shop_tab_without_currency_is_left_alone(entered):
    game = run(Game(shops=[SHOP], balances=[0]), {'shop': {'tabs': [{}]}})
    assert not hasattr(game, 'bought') and 'Scroll' not in ' '.join(game.log)


def test_mismatched_offer_is_cancelled_without_buying(entered):
    game = Game(shops=[SHOP[:4]], balances=[100])
    real = game.screen

    def renamed():
        screen = real()
        if screen.kind != 'event_purchase':
            return screen
        return ExtrasScreen('event_purchase', target=screen.target, maximum=screen.maximum,
                            name='Superior Activity Report', quantity=screen.quantity,
                            unit_cost=screen.unit_cost, total_cost=screen.total_cost)
    game.screen = renamed
    game.fail = lambda message: (_ for _ in ()).throw(RuntimeError(message))
    game.finish = lambda: 'done'
    cancelled = []
    tap = game.tap

    def watch(frame, target, detail):
        if target == (515, 520):
            cancelled.append(detail)
            return
        tap(frame, target, detail)
    game.tap = watch
    with pytest.raises(RuntimeError, match='did not match its card'):
        game.run({**profile(), 'extras': {'shop': {'tabs': [{}]}}})
    assert cancelled and game.balances == [100]


def test_packaged_profile_enables_every_side_reward():
    extras = profile()['extras']
    assert extras['tasks'] is True and extras['cards']['cost'] == 200
    assert len(extras['shop']['tabs']) == 3


def test_unread_balance_skips_its_tab_and_a_small_remainder_may_go_unread(entered):
    # OCR does not find a lone "1" balance, even enlarged.
    skipped = run(Game(shops=[SHOP], balances=[None]), {'shop': {'tabs': [{}]}})
    assert not hasattr(skipped, 'bought') and 'Scroll' not in ' '.join(skipped.log)
    game = Game(shops=[SHOP[:4]], balances=[95])
    real = game.screen

    def small_unread():
        screen = real()
        if screen.kind == 'event_shop' and screen.balance is not None and screen.balance < 10:
            return ExtrasScreen('event_shop', tab=screen.tab, items=screen.items)
        return screen
    game.screen = small_unread
    run(game, {'shop': {'tabs': [{}]}})
    assert game.bought == [('Novice Activity Report', 90), ('Normal Activity Report', 1)]
    assert game.balances == [1]
