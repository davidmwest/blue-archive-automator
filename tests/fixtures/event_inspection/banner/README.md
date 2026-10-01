# Event carousel regression

These 275×90 crops contain only the public event/recruitment banner artwork,
with no account details. Coordinates and OCR boxes are relative to the crop at
canonical `(20, 490)`.

- `departing`: the Aquatic Showdown title is leaving the left edge while a
  recruitment card moves under the old fixed tap. Taken from the failed live
  Treasure Hunt entrance on October 1, 2026 UTC.
- `arriving`: a complete title is visible, but the card is still moving.
- `settled` / `settled-next`: two consecutive stationary event frames. Minor
  compression/shimmer differences should not prevent entry.

JSON files hold the original local OCR observations. The settled title crop is
also the runtime recognition template. Tests reject moving cards and require
a fresh arrival after a nonmatching card, rather than tapping an event card
already visible at the start. This avoids spending the short carousel dwell
time on OCR and a second full-resolution capture.
