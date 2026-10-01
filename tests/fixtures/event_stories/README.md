# Event Story recognition fixtures

Captured during authorized Aquatic Showdown Story validation on September 30,
2026. PNGs use the canonical 1280×720 coordinate space; adjacent JSON files
contain OCR text, confidence, and bounding boxes from the captured screens.
Unneeded account currency balances are masked in both representations. The AP
balance remains visible because it is part of the entry-floor check.

- `detail`: Story 1 entry and its 10 AP projection.
- `dialogue`, `menu`, `skip`: the recognized narrative skip sequence.
- `cleared`: a completed gold book, an available episode, and locked rows.
- `battle_cleared`: the thinner completed gold sword for Story 2.
- `preset`: Story 2's locked guest formation with disabled Quick Formation.

Tests also reject dimmed screens, missing/low-confidence text, an incomplete
formation, and unreadable AP projections. These captures verify the observed
layouts; they do not establish live completion of all 12 episodes.
