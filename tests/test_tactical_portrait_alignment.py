"""Preview alignment from saved pixels, without live OCR or game input."""

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from ba_automator.tactical_battles import Opponent
from ba_automator.tactical_vision import (
    ObservedOpponent, _portrait_similarity, _signature, detail_portrait_alignment, same_opponent,
)
from ba_automator.vision import decode_frame

FIXTURES = Path(__file__).parent / 'fixtures'


def saved_pair(kind='shift'):
    prefix = FIXTURES / f'tactical-portrait-native-{kind}'
    listed = decode_frame(prefix.with_name(prefix.name + '-list.png').read_bytes())
    png = prefix.with_name(prefix.name + '-detail.png').read_bytes()
    detail = decode_frame(png)
    y = 365 if kind == 'shift' else 206
    selected = ObservedOpponent(
        Opponent('listed', 469, 79, (78, 79, 79)), 'Scout', (830, y + 44),
        _signature(listed, (484, y, 526, y + 36)))
    observed = replace(selected, choice=replace(selected.choice, opponent_id='detail'),
                       signature=_signature(detail, (278, 179, 320, 215)))
    return selected, observed, png


def test_saved_native_preview_recovers_two_pixel_displacement_only():
    selected, observed, png = saved_pair()
    assert not same_opponent(selected, observed)
    dx, dy, correlation = detail_portrait_alignment(selected, observed, png)
    assert (dx, dy) == (0, 2)
    assert correlation >= .98
    # Registration doesn't rewrite history or turn the ordinary matcher lenient.
    assert not same_opponent(selected, observed)


@pytest.mark.parametrize('kind', ['native', '720p'])
def test_previously_aligned_native_and_720p_portraits_keep_baseline_match(kind):
    if kind == 'native':
        selected, observed, _ = saved_pair('aligned')
    else:
        listed = decode_frame((FIXTURES / 'tactical-battles-opponents.png').read_bytes())
        detail = decode_frame((FIXTURES / 'tactical-battles-opponent-detail.png').read_bytes())
        selected, observed, _ = saved_pair()
        selected = replace(selected, signature=_signature(listed, (484, 206, 526, 242)))
        observed = replace(observed, signature=_signature(detail, (278, 179, 320, 215)))
    assert same_opponent(selected, observed)


@pytest.mark.parametrize('changed', ['name', 'empty_name', 'account', 'avatar',
                                    'blank', 'malformed', 'wrong_capture'])
def test_registration_never_substitutes_another_identity_or_capture(changed):
    selected, observed, png = saved_pair()
    if changed == 'name':
        observed = replace(observed, name='Another scout')
    elif changed == 'empty_name':
        selected, observed = replace(selected, name=''), replace(observed, name='')
    elif changed == 'account':
        observed = replace(observed, choice=replace(observed.choice, level=80))
    elif changed == 'avatar':
        listed = decode_frame((FIXTURES / 'tactical-portrait-native-shift-list.png').read_bytes())
        selected = replace(selected, signature=_signature(listed, (484, 206, 526, 242)))
    elif changed in ('blank', 'malformed'):
        selected = replace(selected, signature='00' * 576 if changed == 'blank' else 'not hex')
    else:
        png = saved_pair('aligned')[2]
    assert detail_portrait_alignment(selected, observed, png) is None


def test_preview_registration_preserves_normalized_name_matching():
    selected, observed, png = saved_pair()
    observed = replace(observed, name=' ＳＣＯＵＴ ')
    assert detail_portrait_alignment(selected, observed, png) is not None


def test_registration_does_not_search_beyond_two_canonical_pixels():
    selected, observed, png = saved_pair()
    frame = decode_frame(png)
    # The saved +2px displacement becomes +5px; all candidate translations
    # remain below the stricter fallback threshold.
    moved = np.zeros_like(frame)
    moved[3:] = frame[:-3]
    observed = replace(observed, signature=_signature(moved, (278, 179, 320, 215)))
    assert detail_portrait_alignment(selected, observed, cv2.imencode('.png', moved)[1].tobytes()) is None


def test_registration_does_not_accept_a_weak_shifted_match():
    selected, observed, png = saved_pair()
    pixels = np.frombuffer(bytes.fromhex(selected.signature), np.uint8).astype(float)
    rng = np.random.default_rng(141)
    noisy = np.clip(pixels + rng.normal(0, 16, pixels.shape), 0, 255).astype(np.uint8)
    selected = replace(selected, signature=noisy.tobytes().hex())
    registered = _signature(decode_frame(png), (278, 181, 320, 217))
    assert .82 <= _portrait_similarity(selected.signature, registered) < .95
    assert detail_portrait_alignment(selected, observed, png) is None
