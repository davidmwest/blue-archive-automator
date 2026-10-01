# Event Story recognition fixtures

Captured during authorized Aquatic Showdown Story validation on September 30 and
October 1, 2026. PNGs use the canonical 1280×720 coordinate space, except for the
native 2560×1440 reward fixture; adjacent JSON
files contain OCR text, confidence, and bounding boxes from the captured screens.
Unneeded account currency balances are masked in both representations. The AP
balance remains visible because it is part of the entry-floor check.

- `detail`: Story 1 entry and its 10 AP projection.
- `dialogue`, `menu`, `skip`: the recognized narrative skip sequence.
- `cleared`: a completed gold book, an available episode, and locked rows.
- `battle_cleared`: the thinner completed gold sword for Story 2.
- `preset`: Story 2's locked guest formation with disabled Quick Formation.
- `preset_five`: Story 10's locked team, with three strikers, two specials, and
  one empty striker slot. Disabled Quick Formation is expected here.
- `reward_merged_native`: Story 11's reward overlay. Full-frame native OCR
  merges its white continue prompt with the dark stage title behind it. The
  isolated foreground read must still match the complete prompt exactly.

Tests also reject dimmed screens, missing/low-confidence text, an active formation
editor or a formation with no recognizable striker, and unreadable AP projections.
These captures verify the observed layouts; live completion evidence is documented
in [event farming](../../../docs/event-farming.md).
