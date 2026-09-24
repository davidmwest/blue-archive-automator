# Screenshot fixtures

These samples were captured from the authorized local Blue Archive staging instance
on 2026-09-23: global English client, 1280×720, 320 DPI.

Only the relevant dialog or interface controls remain visible. Everything else is
black. They contain no account names, IDs, currency balances, credentials, or tokens.
The test images preserve original pixel positions so the complete OCR/template
pipeline can be tested without a running game.

- `download_prompt.png`: the actual 332.05 MB required-download confirmation.
- `title.png`: the title-screen start control, including OCR's merged-word case.
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
