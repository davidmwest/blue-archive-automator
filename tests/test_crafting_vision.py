from pathlib import Path
from dataclasses import replace
import cv2
import pytest

from ba_automator.crafting_vision import CraftVision, classify_crafting
from ba_automator.vision import StartupVision, Word, decode_frame

FIXTURES = Path(__file__).parent/'fixtures'

@pytest.fixture(scope='module')
def vision():
    return CraftVision(StartupVision())

@pytest.mark.parametrize(('fixture','kind'),[('empty','list'),('quick','quick'),('maximum','quick'),('confirm','confirm'),('running','list'),('zero-keys','quick')])
def test_real_sanitized_quick_craft_screens(vision, fixture, kind):
    result = vision.analyze((FIXTURES/f'crafting-{fixture}.png').read_bytes())
    assert result.kind == kind
    if fixture == 'maximum':
        assert (result.quantity,result.owned,result.required,result.credits)==(3,3,3,6000)
    if fixture == 'zero-keys':
        assert result.owned == 0 and result.required == 1
    if fixture == 'running':
        assert [slot.seconds for slot in result.slots] == [5307,10707,10707]
        assert result.collect_target is None
        assert all(slot.kind == 'running' for slot in result.slots)


def test_dimmed_background_never_authorizes_list_or_craft(vision):
    for fixture in ['empty','quick','maximum','running']:
        frame=decode_frame((FIXTURES/f'crafting-{fixture}.png').read_bytes())
        words=vision.startup.read(frame)
        result=classify_crafting((frame*.5).astype('uint8'),words,keystone=True)
        assert result.kind == 'unknown'


def test_no_node_setup_and_use_off_disable_without_editing_preset(vision):
    frame=decode_frame((FIXTURES/'crafting-quick.png').read_bytes())
    words=vision.startup.read(frame)
    off=[replace(w,text='Use Off') if w.normalized=='use on' else w for w in words]
    assert classify_crafting(frame,off,keystone=True).kind=='disabled'
    unset=[w for w in words if w.normalized not in {'edit','use on'}]
    unset.append(Word('Settings',1,(350,250,490,290)))
    assert classify_crafting(frame,unset,keystone=True).kind=='disabled'
    assert classify_crafting(frame,words,keystone=False).kind=='disabled'


def test_clock_parse_failure_does_not_turn_instant_completion_into_collection(vision):
    frame=decode_frame((FIXTURES/'crafting-running.png').read_bytes())
    words=[w for w in vision.startup.read(frame) if ':' not in w.text]
    result=classify_crafting(frame,words)
    assert result.kind=='unknown' and result.target is None


def test_unreadable_extra_material_card_still_prevents_spending(vision):
    frame=decode_frame((FIXTURES/'crafting-quick.png').read_bytes())
    words=vision.startup.read(frame)
    frame[200:290,960:1040]=255
    result=classify_crafting(frame,words,keystone=True)
    assert result.kind=='disabled' and 'unsupported materials' in result.reason


def test_conflicting_empty_and_running_slot_labels_reject_the_frame(vision):
    frame=decode_frame((FIXTURES/'crafting-running.png').read_bytes())
    words=vision.startup.read(frame)
    words.append(Word('Start Crafting',1,(870,270,1020,310)))
    assert classify_crafting(frame,words).kind=='unknown'


def test_natural_completion_requires_zero_timer_and_yellow_receive(vision):
    frame=decode_frame((FIXTURES/'craft-one-ready.png').read_bytes())
    words=vision.startup.read(frame)
    result=classify_crafting(frame,words)
    assert [s.kind for s in result.slots]==['ready','running','running']
    assert result.collect_target==(1120,618)
    missing=[w for w in words if w.normalized!='receive']
    assert classify_crafting(frame,missing).kind=='unknown'
    not_zero=[replace(w,text='00:00:01') if w.text=='00:00:00' else w for w in words]
    assert classify_crafting(frame,not_zero).kind=='unknown'
    frame[590:645,1040:1210]=128
    assert classify_crafting(frame,words).collect_target is None


def test_live_craft_receipt_requires_continue_label(vision):
    frame=decode_frame((FIXTURES/'craft-collected.png').read_bytes())
    words=vision.startup.read(frame)
    result=classify_crafting(frame,words)
    assert result.kind=='receipt' and result.target==(640,631)
    assert classify_crafting(frame,[w for w in words if w.normalized!='touch to continue']).kind=='unknown'
