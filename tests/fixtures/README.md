# Screenshot fixtures

These samples were captured from the authorized local Blue Archive staging instance
on 2026-09-23: global English client, 1280×720, 320 DPI.

Only the relevant dialog or interface controls remain visible. Everything else is
black. They contain no account names, IDs, currency balances, credentials, or tokens.
The test images preserve original pixel positions so the complete OCR/template
pipeline can be tested without a running game.

- `download_prompt.png`: the actual 332.05 MB required-download confirmation.
- `title.png`: the title-screen start control, including OCR's merged-word case.
- `publisher_splash.png`: the publisher logos on black, classified as loading
  without extending the overall startup timeout or sending input.
- `banner_day.png`, `banner_night.png`: announcement close controls over different
  backgrounds, paired with the announcement footer.
- `home_controls.png`: the two unobstructed home menu anchors.
- `news_overlay.png`: the announcement portal's close control/sidebar with dimmed
  home anchors, ensuring this overlay is dismissed before reporting success.
- `new_products.png`: the store promotion notice; its Confirm button dismisses it,
  while Shortcut is deliberately excluded from startup actions.

Small matching templates in `ba_automator/assets` come from the same local captures.

Cafe samples retain only the relevant controls and scene fragments. `cafe_markers`
contains the Cafe HUD and yellow attention rays; `cafe_heart_before/after` contains
the student and newly visible relationship heart. `cafe_earnings` and `cafe_receipt`
retain the earnings dialog and the verified 81 AP / 73,957 credit receipt. The
account's top bar, name, total balances, and other personal information are removed.

Lessons samples (`lesson-*.png`) were captured on 2026-09-24 UTC from the same
authorized instance. They retain only the location headers, ticket/XP controls,
room grid, or lesson confirmation needed for recognition. The top account resource
bar is removed. Paired JSON files record local OCR output; `bond_crops` identify
small heart labels checked against the capture. Tests replay these observations
without loading an OCR model. The room grids include unowned students with pink
hair so ownership cannot be inferred from artwork, and both single-line and wrapped
room names. These game-derived fixtures remain outside the project's MIT grant;
see `THIRD_PARTY_NOTICES.md` at the repository root.

Lessons also includes an opening-animation rejection sample, a captured relationship
rank-up banner, loading/completed Lesson Report panels, and before/after room grids
showing the completed portrait state. Area/school rank-up tests construct synthetic
images in code; no screenshot or live validation is claimed for that popup.

`lesson-ticket-four` preserves the actual 4/7 counter after a verified lesson.
Whole-frame OCR omitted that ratio; `ticket_crops` record its independently matching
2× and 3× local OCR readings. Other remaining counts are synthetic OCR cases in the
tests, not additional claims of captured gameplay.
