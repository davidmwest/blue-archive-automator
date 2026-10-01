"""A fully visible one-card receipt needs no tooltip or scrolling inputs."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from ba_automator import loot_receipts as lr
from ba_automator.config import Config
from ba_automator.runtime import Capture, TaskError
from ba_automator.vision import StartupVision


@pytest.fixture(scope='module')
def receipt():
    png = (Path(__file__).parent / 'fixtures' / 'loot-treasure-single-credits-native.png').read_bytes()
    vision = StartupVision()
    parsed = lr.page(png, vision)
    assert parsed.kind == 'reward' and len(parsed.cards) == 1
    assert parsed.cards[0].name == 'Credit Points'
    assert parsed.cards[0].quantity == 40000
    return png, vision, parsed


@pytest.fixture
def reader(receipt, tmp_path):
    png, vision, parsed = receipt
    clock = SimpleNamespace(now=0.)
    def sleep(seconds):
        clock.now += seconds
    def fail(message):
        raise TaskError(message, tmp_path)
    config = Config(serial='127.0.0.1:5695', package='com.nexon.bluearchive',
                    state_dir=tmp_path / 'state', run_dir=tmp_path / 'runs')
    runner = SimpleNamespace(clock=lambda: clock.now, sleep=sleep, fail=fail,
                             config=config, task='event_treasure', journal=Mock())
    result = lr.ReceiptReader(runner, vision, tmp_path / 'receipt.png')
    result.capture = Mock(side_effect=lambda **kw: Capture(png, clock.now, config.package))
    result.pan = Mock(side_effect=AssertionError('Single reward should never scroll'))
    result.inspect_card = Mock(side_effect=AssertionError('Single reward should never tap'))
    return result


def test_real_treasure_credits_saved_once_without_inputs(reader, receipt):
    result = reader.run()
    assert result['items_complete']
    item, = result['items']
    assert (item['name'], item['quantity']) == ('Credit Points', 40000)
    assert (reader.r.config.state_dir / 'loot-icons' / (item['icon_id'] + '.png')).is_file()
    assert reader.inputs == 0
    assert reader.r.clock() >= .5  # Two stable frames before the shortcut.
    assert reader.observed[0] == receipt[0]
    events = (reader.r.config.state_dir / 'important-actions.jsonl').read_text().splitlines()
    assert len(events) == 1 and json.loads(events[0])['action'] == 'loot_received'


def test_initial_unrecognized_heading_retries_without_inputs(reader):
    read = reader.read
    calls = 0
    def transient(cap):
        nonlocal calls
        calls += 1
        return lr.Page('unknown') if calls <= 2 else read(cap)
    reader.read = transient
    result = reader.run()
    assert result['items_complete']
    assert result['items'][0]['quantity'] == 40000
    assert reader.inputs == 0
    assert reader.r.clock() >= 1.5


def test_persistently_unrecognized_receipt_stops_after_bounded_observation(reader):
    reader.read = Mock(return_value=lr.Page('unknown'))
    result = reader.run()
    assert not result['items_complete'] and result['items'] == []
    assert reader.capture.call_count == 4
    assert reader.inputs == 0
    assert reader.r.clock() == 1.5


@pytest.mark.parametrize('change', ['clipped', 'leading_clipped', 'trailing_clipped',
                                   'expand', 'off_center', 'small', 'unnamed', 'no_quantity',
                                   'no_icon', 'two_cards'])
def test_single_card_shortcut_rejects_incomplete_layouts(reader, receipt, change):
    png, _, parsed = receipt
    card = parsed.cards[0]
    if change in {'clipped', 'leading_clipped', 'trailing_clipped'}:
        parsed = replace(parsed, **{change: True})
    elif change == 'expand':
        parsed = replace(parsed, expand_target=(100, 100))
    elif change == 'two_cards':
        parsed = replace(parsed, cards=(card, card))
    else:
        change_card = {'off_center': {'box': (100, 250, 148, 222)},
                       'small': {'box': (590, 260, 100, 175)},
                       'unnamed': {'name': None}, 'no_quantity': {'quantity': None},
                       'no_icon': {'icon': b''}}[change]
        parsed = replace(parsed, cards=(replace(card, **change_card),))
    assert reader.single_reward(Capture(png, 0., 'game'), parsed) is None
    reader.capture.assert_not_called()


def test_single_card_requires_confident_agreeing_name_and_quantity(reader, receipt, monkeypatch):
    png, _, parsed = receipt
    def name(*args, **kwargs):
        assert kwargs['minimum_confidence'] == .95
        return None
    monkeypatch.setattr(lr, 'reward_name', name)
    assert reader.single_reward(Capture(png, 0., 'game'), parsed) is None
    reader.capture.assert_not_called()


def test_single_card_does_not_accept_a_changed_fresh_frame(reader, receipt, monkeypatch):
    png, _, parsed = receipt
    monkeypatch.setattr(lr, 'same_receipt_view', lambda *a, **kw: False)
    with pytest.raises(TaskError, match='changed during verification'):
        reader.single_reward(Capture(png, 0., 'game'), parsed)
    assert reader.capture.call_count == 4
    assert reader.inputs == 0
    assert reader.r.journal.save_image.call_count == 4
    reader.r.journal.save_image.assert_called_with('receipt-single-card-rejected-4.png', png)


def test_single_card_retries_only_against_original_receipt(reader, receipt, monkeypatch):
    png, _, parsed = receipt
    seen = []
    def compare(before, after, expected, **kwargs):
        seen.append((before, after, expected))
        return len(seen) == 3
    monkeypatch.setattr(lr, 'same_receipt_view', compare)
    frames = [Capture(b'rejected-one', 0., 'game'), Capture(b'rejected-two', .25, 'game'),
              Capture(png, .5, 'game')]
    reader.capture.side_effect = frames
    checkpoint = Mock()
    reader.r.receipt_checkpoint = checkpoint
    result = reader.single_reward(Capture(png, 0., 'game'), parsed)
    assert result['name'] == 'Credit Points' and result['quantity'] == 40000
    assert [entry[0] for entry in seen] == [png] * 3
    assert all(entry[2] is parsed for entry in seen)
    checkpoint.assert_called_once_with(frames[-1], reader.evidence)
    assert reader.inputs == 0


def test_single_card_checks_freshness_after_expensive_comparison(reader, receipt, monkeypatch):
    png, _, parsed = receipt
    def compare(*args, **kwargs):
        reader.r.sleep(60.)
        return True
    monkeypatch.setattr(lr, 'same_receipt_view', compare)
    with pytest.raises(TaskError, match='changed during verification'):
        reader.single_reward(Capture(png, 0., 'game'), parsed)
    assert reader.capture.call_count == 4
    assert reader.inputs == 0


def test_observed_credit_heading_sparkle_settles_without_loosening_identity(reader):
    fixtures = Path(__file__).parent / 'fixtures'
    before, sparkle, settled = [
        (fixtures / f'loot-single-credit-{suffix}-native.png').read_bytes()
        for suffix in ('reference', 'sparkle', 'settled')
    ]
    parsed = lr.page(before, reader.vision)
    assert [(card.name, card.quantity) for card in parsed.cards] == [('Credit Points', 40000)]
    assert not lr.same_receipt_view(before, sparkle, parsed, vision=reader.vision)
    assert lr.same_receipt_view(before, settled, parsed, vision=reader.vision)
    reader.capture.side_effect = [Capture(sparkle, 0., 'game'), Capture(settled, .25, 'game')]
    result = reader.single_reward(Capture(before, 0., 'game'), parsed)
    assert result['quantity'] == 40000 and result['name'] == 'Credit Points'
    assert reader.observed[0] == settled
    assert reader.capture.call_count == 2 and reader.inputs == 0


def test_single_card_requires_quantity_agreement(reader, receipt, monkeypatch):
    png, _, parsed = receipt
    def quantity(*args, **kwargs):
        assert kwargs['minimum_confidence'] == .95
        return 4000
    monkeypatch.setattr(lr, 'reward_quantity', quantity)
    assert reader.single_reward(Capture(png, 0., 'game'), parsed) is None
    reader.capture.assert_not_called()
