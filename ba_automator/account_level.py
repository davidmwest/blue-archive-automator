"""Recognize the account level celebration over a paid sweep's receipt."""

import re

import numpy as np

from .crafting_vision import within
from .vision import decode_frame, read_game_words


def read_sweep_level_up(png, vision):
    """Require the blue band, sweep title, level, and continue prompt together.

    The cheap band check avoids OCR on every ordinary reward capture. It is
    only a candidate signal; it never authorizes a tap by itself.
    """
    frame = decode_frame(png)
    for left, right in ((20, 120), (1160, 1260)):
        band = frame[320:400, left:right].astype(np.int16)
        if ((band[:, :, 0] > 120) &
                (band[:, :, 0] - band[:, :, 2] > 20)).mean() < .65:
            return None
    words = read_game_words(png, vision)

    def label(text, bounds):
        return any(w.normalized.replace(' ', '') == text.replace(' ', '') and w.confidence >= .9
                   for w in within(words, bounds))

    if not (label('sweep complete', (450, 65, 835, 125))
            and label('level up', (470, 185, 805, 280))
            and label('touch to continue', (460, 550, 820, 620))):
        return None
    levels = [int(w.text) for w in within(words, (585, 335, 695, 410))
              if w.confidence >= .95 and re.fullmatch(r'[1-9]\d{0,2}', w.text)]
    return levels[0] if len(levels) == 1 else None
