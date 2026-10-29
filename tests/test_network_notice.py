"""The game's unstable-network notice is answered with its own Reconnect button."""

import numpy as np
import pytest

from ba_automator.shop_vision import network_reconnect
from ba_automator.vision import Word
from test_faq_overlay import overlay, runner

MESSAGE = Word('The network connection is unstable.', 0.99, (420, 290, 860, 322))
RECONNECT = Word('Reconnect', 0.99, (705, 488, 825, 522))
CONFIRM = Word('Confirm', 0.99, (450, 488, 570, 522))


def test_notice_targets_reconnect():
    assert network_reconnect([MESSAGE, CONFIRM, RECONNECT]) == RECONNECT.center


@pytest.mark.parametrize('words', [
    [],
    [CONFIRM, RECONNECT],                                       # no notice text
    [MESSAGE, CONFIRM],                                         # no Reconnect button
    [MESSAGE, CONFIRM, Word('Reconnect', 0.6, RECONNECT.box)],  # unsure read
    [Word('The network connection is unstable.', 0.99, (420, 600, 860, 632)), RECONNECT],
])
def test_other_screens_do_not_match(words):
    assert network_reconnect(words) is None


def test_wait_reconnects_and_then_sees_the_screen(tmp_path):
    # Live, the notice covered Cafe 2 and the visit gave up after 30 seconds.
    clear = np.full((720, 1280, 3), (70, 60, 50), np.uint8)
    notice = overlay()  # a white dialog; without an FAQ heading it is not that overlay
    instance, device = runner(tmp_path, [notice, clear], [MESSAGE, CONFIRM, RECONNECT])
    try:
        assert instance.wait('menu').screen.kind == 'menu'
    finally:
        instance.journal.close()
    assert device.taps == [RECONNECT.center]


def test_wait_reconnects_at_most_three_times(tmp_path):
    notice = overlay()
    instance, device = runner(tmp_path, [notice], [MESSAGE, CONFIRM, RECONNECT])
    ticks = iter(range(0, 1000))
    instance.clock = lambda: next(ticks) * 1.0
    try:
        with pytest.raises(Exception, match='did not reach menu'):
            instance.wait('menu', timeout=60)
    finally:
        instance.journal.close()
    assert device.taps == [RECONNECT.center] * 3
