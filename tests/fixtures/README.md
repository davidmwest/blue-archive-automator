# Screenshot fixtures

These samples were captured from the authorized local Blue Archive staging instance
on 2026-09-23: global English client, 1280×720, 320 DPI.

For the startup samples listed below, only the relevant dialog or interface controls remain visible. Everything else is black. They contain no account names, IDs, currency balances, credentials, or tokens. Later task-specific samples and their masking are described separately below.
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

`lesson-hyakki-shopping-grid` and `lesson-hyakki-shopping-preview` preserve the
same room before spending a ticket. Along with `lesson-haruhabara-headers`, they
cover wrapped titles where tightly cropped OCR duplicated letter fragments. Their
`header_crops` record OCR with an eight-pixel white margin before enlargement;
the saved game screenshots themselves retain their original pixels.

Crafting samples (`crafting-*.png`) were captured on 2026-09-24. They retain the synthesis slot list, configured Quick Craft preset, maximum batch, confirmation, running timers, and red zero-inventory counter. The account resource bar is removed. `craft_keystone.png` is a small matching crop of the keystone material icon. Natural completion and collection recognition now have live fixtures and passed runtime verification.

`crafting-unaffordable-max.png` preserves the evening live failure where Max
selected three crafts despite only two owned keystones. The red 2/3 material
counter, quantity 3, and 6,000-credit fee remain; the account resource bar is
masked. It verifies recognition of an unaffordable selection, not a completed
craft start.

Pack/mail samples (`packs-*.png`) were captured on 2026-09-24. Store backgrounds and account identity are masked; mailbox resource balances are removed. Google Play fixtures retain only the product/price/button or the optional backup-payment prompt, with account/payment information and underlying game identity removed. Tests also remove OCR status words to ensure the remaining ownership shading still prevents a duplicate purchase.

`craft-one-ready.png` and `craft-collected.png` cover the first natural craft completion and receipt; the account resource bar is masked.

AP samples (`ap-*.png`) retain the AP counters needed to verify spending, while masking credit/Pyroxene balances and the receipt’s account nickname. They cover commission selection, Hard stars and remaining attempts, the confirmation, and the receipt. The helper-icon, small-index, and split-title samples preserve OCR failures found during live surveys and sweeps. Hard details retain both current and projected attempt counts, including a last-available-attempt case.

Task reward samples (`task-rewards-*.png`) were captured on 2026-09-24 at 1280×720. They retain only the Tasks notification and home anchors, page controls, or reward header/cards/continue label. Account identity, balances, the mascot illustration, and unrelated background are masked. They cover active/absent badges, enabled Claim All, the separate daily completion reward, an empty page, and both ends of a scrolling receipt.

Home notification samples (`red-dots-*.png`) were captured on 2026-09-24. They mask account identity/balances and private Club details, retaining the fixed badges, Social cards, Free Daily Pack price/confirmation/exhausted state, attendance notice, and receipts. No paid checkout was entered for these captures.

Tactical Challenge samples (`tactical-*.png`) were captured on 2026-09-24 local time. Account and opponent identities/ranks are masked. They retain an available Time Reward, the disabled controls after collection, and its actual 70,570-credit receipt. The enabled Daily Reward color state is synthesized only inside a test; it is not presented as a live daily-claim capture.

Loot inspection fixtures (`loot-*.png`) were captured from the authorized staging
instance on September 25 UTC (September 24 local). They preserve only reward
panels and tooltips; player identity, balances, and XP were removed. The fixtures
cover wrapped names, currency tooltips without Owned, a cyan title divider that
OCR previously misread as an extra `I`, Final-only sweep accounting,
and the magnifier/Full List overflow layout. `ap-regenerated-detail.png` retains
the AP values needed to reproduce the stale preview after natural regeneration,
with other account balances removed. Game artwork remains subject to the
third-party notices, outside the project's MIT grant.

`loot-daily-receipt-before.png` and `loot-daily-receipt-after.png` preserve the
same live Tactical Challenge daily receipt from September 25 UTC: 18 Pyroxenes
and 70 Tactical Challenge Coins. Only the receipt heading and two reward cards
remain; all account and opponent information is removed. The pair retains the
Pyroxene artwork's animation so fast input revalidation can be tested without
requiring identical animated icons or extending screenshot freshness deadlines.

Total Assault navigation samples (`assault-menu`, `assault-detail`,
`assault-formation`, `assault-formation-empty`, and `assault-quick`) were captured
on September 25 UTC from the authorized staging instance. Account balances,
season rank/points, and unrelated character backgrounds are masked. The menu
retains event dates and the ticket counter; the formation retains its six student
nameplates, levels, numbered stars, and damage-color bands. Paired JSON files
contain only the retained controls' local OCR observations. These fixtures verify
navigation and team reading, not a mock or real battle victory. Star digits are
read together from enlarged crops in one additional local OCR invocation.

`assault-battle-hud` retains the actual mock battle timer, boss name/HP ratio,
and white (off) AUTO control. Best rank points and the battle scene are masked.
`assault-battle-auto-on` retains the actual yellow enabled control and a clipped
Mock Battle label during a skill cut-in. Missing mock text is not interpreted as
a real battle. Both samples mask the battle scene and best rank points; neither
fixture establishes a battle result.

`assault-battle-result` and `assault-damage-report` preserve the first mock's
Battle Complete heading, elapsed time, controls, and six exact student damage
totals. The result's ranking points and character scene are masked. The first
mock's subsequent Close Call achievement independently corroborated its victory.
The win recognizer requires the gold Battle Complete heading and lower-right
cyan Confirm together; the BAAS author's separate English defeat layout instead
places Confirm centrally. Elapsed time is not remaining time, and the character
models do not establish surviving-student counts.

`assault-assistant-formation` retains the selected six student nameplates after
the assistant selection. The two blue unique-equipment star counts establish
base rarity five; animated switching between gold rarity and blue equipment
stars must not change the selected team's fingerprint. This screen does not
identify the lender, which must be bound from the assistant-selection evidence.

`assault-assistant-{list,mystic-list,aris-preview,page2,page3,remove,mystic-filter,owned}`
were captured from the same staging instance on September 25 UTC. Account bars
are masked, and lender names are replaced with synthetic `Loan N` labels in both
the images and paired OCR observations. They retain the observed assistant
selection, attack-type filter, sort controls, and selected student preview.
The three assistant sort/marker recognition assets are crops of these captures.
`ba_automator/assets/assault_damage_report.png` is cropped from the sanitized
`assault-battle-result` fixture. These local game-derived assets have the same
license exclusion as the other recognition templates.

The neutral loss-heading layout used by offline battle tests is synthesized;
the report-control glyph comes from the captured winning result. It tests the
loss recognizer's guards, not a live defeat or timeout recovery. No defeated
mock result has been captured at this checkpoint.

`assault-assistant-real-quick-empty` records real-entry formation resetting the
borrowed slot on September 25, 2026. It retains the empty second striker slot;
account bars are masked in the image and OCR. `assault-assistant-retained` records
reopening Quick Formation after restoring the assistant. The left inspection
panel is blank, but the selected offering and sole second-slot assistant badge
remain visible. Lender labels are replaced with synthetic `Loan N` values.

`assault-assistant-fee` records the real Mobilize confirmation: one assistant,
40,000 credits, and a yellow Confirm. Account bars are masked. Three small
`assault-assistant-fee-*` templates preserve its assistant badge, credit icon,
and base-five-star marker. They are local game-derived recognition crops,
excluded from the repository's source-code license like the other game assets.
A confirmation screenshot alone does not establish that the fee was deducted.

`assault-entry-confirmation` is the next live notice after the assistant fee:
Hardcore, "Use Total Assault Ticket to enter?", and the observed 6→5 projection.
The top account bar is masked. Entry recognition requires the difficulty and
one-ticket projection; the battle driver separately matches them to the durable
entry intent before acknowledging this notice.

`assault-clear-receipt.png/json` is the first real Hardcore reward page captured
locally on September 25, 2026. The dimmed battle score, time, and team strips
are masked; matching OCR is removed. Its two item cards and the distinct Confirm
and Go to Lobby controls remain unchanged. Reward quantities are parsed from
the receipt, not inferred from difficulty.

`assault-season-record.png/json` records the subsequent Best Season Record
Reached notice from that real clear. Only the modal is retained; account rank
and personal point totals inside it are masked in both pixels and OCR. Its
Confirm acknowledges a record, not a reward claim.

`assault-sweep-detail.png/json` is the post-clear Hardcore Room Info panel with
one sweep selected, five tickets, the 5→4 projection, and an enabled Max button.
Only Room Info and event dates are retained; the surrounding account and rank
information are masked. The runner must independently verify the resulting
count and ticket projection after changing the count.

`assault-sweep-confirmation.png/json` is the following live five-ticket sweep
notice. Only its modal and the visible Room Info tab, Hardcore label, and 5→0
projection remain. These fields independently establish the requested sweep
count and tier; the partially obscured boss name is masked and never inferred.
The runner separately binds the notice to its persisted sweep intent.

`assault-sweep-receipt.png/json` records the five completed sweeps on September
25, 2026. The account name, experience strip, and background are masked. Only
the Final row is totaled: 500 Total Assault Coins and 50 Advanced Total Assault
Coins, independently read by the shared card reader.
`assault-zero-detail.png/json` records the resulting Room Info panel, retaining
only the room and event dates. Zero tickets require the explicit `0→-` entry
projection and zero selected sweeps; absent text is never interpreted as zero.
