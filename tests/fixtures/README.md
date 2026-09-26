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

`cafe-edge-drift-before/after.png` retain only the camera-measurement scene crop
from a September 25 UTC Cafe 2 normalization failure. A commanded rightward
drag moved the isometric scene approximately 60 pixels right and 31 pixels up.
They preserve arbitrary furniture and moving students while removing the entire
HUD, account resource bar, and visit timer.

`cafe-home-label.png` preserves the Cafe icon/label and the Campaign home anchor
from a September 26 UTC failed entry. Three taps on the cup illustration left
the game at Home; the fixture verifies selection of the label's matched center
and rejection when either home anchor is missing or dimmed. All other pixels,
including account identity, currency balances, and the home character, are black.
This is recognition evidence, not a claim that the revised target has run live.

Lessons samples (`lesson-*.png`) were captured on 2026-09-24 UTC from the same
authorized instance. They retain only the location headers, ticket/XP controls,
room grid, or lesson confirmation needed for recognition. The top account resource
bar is removed. Paired JSON files record local OCR output; `bond_crops` identify
small heart labels checked against the capture. Tests replay these observations
without loading an OCR model. The room grids include unowned students with pink
hair so ownership cannot be inferred from artwork, and both single-line and wrapped
room names. These game-derived fixtures remain outside the project's MIT grant;
see `THIRD_PARTY_NOTICES.md` at the repository root.

`lesson-shiratori-single-bond` and `lesson-redwinter-single-bond` are September 26
room grids with thin relationship-rank `1` labels. The original crops' low-confidence
readings and the fallback's agreeing 3×/5× readings are preserved in paired JSON.
Only the lesson modal remains visible; all account resources and background are black.

`tickets-scrimmage-split-projection.png` and `tickets-bounty-quantity-seven.png`
are September 26 pre-spend Mission Info controls. They retain the task heading,
AP needed for cost verification, and mission dialog; credit/Pyroxene balances
and unrelated background are black. They cover separate AP/ticket OCR words
and a quantity selector that overshot its requested count. Neither is a receipt.

`tickets-bounty-zero-selector.png` retains the Bounty heading, AP verification
counter, and Mission Info dialog after the September 26 Classroom H sweep spent
its final five tickets. Whole-frame OCR omits the dash in the visible `0 → -`
projection; independent 2× and 3× crops recover it. All unrelated background,
credit/Pyroxene balances, and other account HUD are black. This is post-spend
balance evidence; the exhausted selector never supplies a Sweep target. The
paired JSON retains only original OCR words inside the unmasked regions, because
masking the screenshot itself changes whole-frame text detection. Tests replay
those observations and run real OCR on the unchanged enlarged ticket bubble.

`tickets-scrimmage-zero-missing-projection.png/json` records the final Millennium B
selector from September 26. Whole-frame OCR misses the entire exhausted ticket
label beside the AP projection. Only the task heading, AP verification counter,
and Mission Info dialog remain visible. The fallback isolates the ticket text
from the neighboring AP digits, adds blank margins, and requires agreeing 2×/3×
reads of `0 → -`. Tests reject conflicting or low-confidence reads, nonzero
quantities, changed AP, and missing stage or screen identity. This post-spend
observation never supplies a Sweep target.

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

`crafting-home-label.png` comes from the September 26 UTC Crafting navigation
failure. Only the two home menu anchors and Crafting icon/label remain. Account
identity, balances, time, notifications, and the character background are masked.
The runner had stayed on home after three accepted taps on the illustration;
this sample verifies recognition of the lower text-label target. It does not
establish that Android delivered a tap or that a live navigation succeeded.

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

`loot-craft-gifts-before/after.png` retain the two-gift receipt from a September 25
UTC Crafting failure: one Brain Teaser Puzzle Cube and one Wind-Up Music Box.
The cube artwork sparkled between no-op end-of-list swipes, causing the old
exact-icon comparison to reject overlap even though both labels and quantities
were unchanged. Only the receipt heading, cards, and continue label remain;
the account resource bar and crafting background are removed.

`loot-compact-fedora.png`, `loot-compact-helmet.png`, and
`loot-compact-broken-outline.png` are compact item-card crops from saved local
staging receipts, preserved without pixel alteration. Fedora and Helmet come
from a September 25 UTC sweep Full List; the broken outline is a Tech Notes
card from a September 24 UTC Lesson Report. They retain item artwork, visible
tier/quantity overlays, and neighboring card-edge fragments; no account identity
or resource balances are present. The Fedora and Helmet crops test removal of
adjacent-card fragments without cutting the complete white frame, artwork,
tier badge, or quantity.
The broken-outline crop tests the conservative fallback that keeps the original
image when the outline cannot be established. Masking changes alpha only;
regression checks preserve all BGR pixels used for receipt comparison. These
game-derived fixtures are excluded from the source-code MIT grant, like the
other recognition samples.

`loot-assault-points-before/after.png` and
`loot-assault-points-merged-heading-before/after.png` retain stationary Total
Assault point-reward receipts captured on September 25 UTC. A sparkle crossing
the heading changes its yellow pixels; OCR also switches between one text box
and two while preserving the exact heading and its position. All complete card
names, quantities, and rectangles stay unchanged. The partial seventh card must
remain uncounted until a later overlapping page reveals it fully.
`loot-assault-points-rebound-before/after.png` instead records the elastic scroll
rebound at the left edge: fractional motion changes card positions and label
pixels, so input freshness must reject it and the reader must wait for settling.
`loot-assault-points-tooltip-return-before/after.png` captures the same receipt
after the second item tooltip was dismissed. Tight heading OCR invents an
accented character where a sparkle crosses the slanted text; full-frame OCR
reads the exact heading at high confidence without relaxing card identity.
These eight images retain only the heading, card viewport, and continue label;
the account bar and the background rank/points are removed.

`loot-assault-points-overlap-before/after.png` retains two overlapping pages
from the same points receipt. The Advanced Activity Report border rasterizes
at 147px wide before scrolling and 148px after scrolling; its complete name and
quantity remain unchanged. The four-card ordered overlap must remain valid
without counting those drops twice. Only the heading, card viewport, and
continue label remain; private account and rank/points information is masked.

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

## Campaign reward notification fixture

`loot-assault-points-heading-space-{before,after}.png` preserve the September 25,
2026 11:33 UTC receipt recovery's final stationary refresh pair. A heading
sparkle makes OCR join `REWARDACQUIRED!` before reading `REWARD ACQUIRED!` in
the next frame, with unchanged heading bounds, card labels, and quantities.
Only the receipt heading, item viewport, and continue label remain; account
balances and rank/points background data are masked. Whitespace normalization
does not relax the exact phrase, high confidence, geometry, or fresh input guards.

`loot-assault-points-clipped-edge-{before,after}.png` preserve a September 25,
2026 partial page from the 11:24 UTC recovery and a subsequent stationary
capture. Broken Quimbaya Relic loses its right border and final label pixels
in the viewport fade, giving a misleading 139–140-pixel fragment. It must stay
unread until a later overlapping page exposes its whole card. Only the receipt
heading, item viewport, and continue label remain; account and rank data are
masked.

`loot-assault-points-delayed-tooltip-{before,after}.png` preserve the September
25, 2026 11:17 UTC recovery's first Credit Points inspection. The screenshot
immediately after its input was still the receipt; its tooltip appeared while
page OCR ran. The pair verifies that this expected transition needs a newly
observed tooltip, never another input authorized by the old receipt. Only the
heading, cards, continue label, and tooltip remain; account and rank background
data are masked.

`loot-assault-points-resumed-scroll-{before,after}.png` show a September 25,
2026 receipt row that resumed moving while OCR ran after a forward swipe.
Its 107-pixel movement must invalidate the old tap rectangles. The five-card
ordered overlap remains readable after observing the new position, including
the split first line of "Broken Quimbaya Relic". Only the receipt heading,
item viewport, and continue label remain; account and rank background data are
masked. These fixtures support re-observation without additional device input.

`loot-assault-points-name-raster-{before,after}.png` preserve a stationary
September 25, 2026 Total Assault points receipt from the 11:00 UTC recovery.
The Advanced Enhancement Stone label differs by at most four RGB values due
to text rasterization; its wording, amount, and card rectangle are unchanged.
Only the receipt heading, item viewport, and continue label remain; account
balances and the rank/points background are masked. They verify exact fresh
name/amount OCR as a fallback when named text pixels change, while altered
wording and missing quantities still fail closed.

`assault-rewards-{menu,panel,rank,points}.png/json` are September 25, 2026
staging captures of the independent reward collector. The resource header,
leaderboard identities, and personal rank/points are masked in pixels and OCR.
This includes the rank/points strip behind and below the reward modal; the
adjacent Rewards control and its notification marker remain visible.
The retained controls show the Total Assault menu, Detailed Rank Info, a disabled
Rank Reward claim, and an enabled Total Points Reward claim. They establish
navigation and claim availability, not receipt delivery. Enabled rank rewards
are tested offline by modifying the captured control; no live rank reward was
available during this checkpoint.

`red-dots-campaign.png` is a September 25, 2026 staging capture. The private AP and currency header is masked. Its Total Assault marker is red and its Tactical Challenge marker is amber. Tests also compare the earlier `ap-campaign-stable.png` amber markers, reject dimmed screens, and remove the red marker to verify that a Campaign label alone cannot request collection. The fixture shows notification evidence, not a reward receipt or a completed claim.

`loot-sweep-dismiss-pulse-{before,after}.png` preserve the exact failed refresh
pair from the September 25, 2026 12:30 UTC Hard 1-2 sweep. The Final row contains
six unchanged reward cards while the neutral tooltip-dismiss pulse fades at
(270, 550), outside those cards and Confirm. Only the Sweep Complete heading
and Final/Confirm panel remain; all account identifiers, resources, experience,
and background pixels are masked. The pair verifies that decorative pulse
pixels cannot invalidate unchanged loot, while changed card artwork, quantity,
heading, or Confirm still fail the same strict pixel guard.

`relationship-rank-up-gift.png/json` is the September 25, 2026 authorized gift
validation: one Wind-Up Music Box raised the selected student from relationship
rank 2 to 3, with the displayed ATK +8. Only the heart rank, celebration heading,
and stat bar remain; the portrait and background are masked. The overlay itself
does not name the student. The caller's separately verified gift confirmation
identified Tsurugi, so tests supply that context explicitly and also verify that
ordinary captures leave the name unknown. Stat totals remain unknown because
the screen exposes only the increment.

`invitation-list-controls.png` retains only September 25, 2026 invitation
sort/search controls and the scrollbar. Student rows, portraits, account HUD,
and all other pixels are masked. The invitation sort and search templates in
`ba_automator/assets/invitation-{sort,search}-*.png` are small crops of those
controls, including ascending/descending and collapsed/expanded states.
Inline OCR fixtures in the invitation tests retain only the row names, ranks,
button labels, and geometry needed by the parser; no account HUD is included.
These fixtures test ordering, empty-search verification, row association,
and bounded scrolling without publishing full account captures.

`invitation-profile-rarity.png` is the 85×26-pixel gold-star strip from the
September 25 live Aris (Maid) profile inspection. It contains no portrait,
student name, or account background. The source profile had five stars;
lower-rarity tests mask later stars and are synthetic variations, not live
captures of all rarities. The complete boundary-rank lookup and invitation
reselection remains offline-tested. These game-derived crops remain outside
the MIT grant described in the third-party notices.

`loot-grid-tier-pulse-{before,after}.png` retain the Full List modal from the
September 25 Hard 13-1 receipt. Both frames show the same ten cards; the outer
cyan outlines of T5/T3 tier badges differ during animation. Regression tests
require independently read, matching tier labels before ignoring those badge
pixels, and keep artwork, quantity, position, and clipped edges guarded.

`loot-grid-dismiss-pulse-{before,after}.png` retain the Full List modal from the
September 26 Bounty receipt. The white tooltip-dismiss pulse fades in unused
space near (350, 545); the twelve full cards and clipped bottom row remain
unchanged. The entire reward viewport and heading/Okay controls remain strict.
All pixels outside these four receipt modals are masked, including account
name, balances, experience, and background. These game-derived fixtures are
excluded from the MIT grant as described in the third-party notices.

`loot-sweep-task-notice-{clear,before,after}.png` retain the September 26 live
Hard 12-1 receipt heading, Final reward row, Confirm button, and transient
task-completion notice. The clear frame precedes the notice; the other two
show its animation over the heading after the third Hard sweep. The Final
rewards remain three Novice Activity Reports and 1,263 Credits. Tests require
bounded observation without input until this notice leaves, followed by the
unchanged strict receipt guard. Account identifiers, resource balances, and
experience are masked. These game-derived crops are excluded from MIT.

`loot-tooltip-pointer-pulse-{before,after}.png` retain only the General Hairpin
Blueprint tooltip, nearby Final reward cards, and receipt heading/Confirm
control from the September 26 live Hard 11-3 receipt. The selected-card glow
briefly disconnects the cyan pointer from the tooltip outline. Its rectangular text panel remains unchanged.
Tests anchor that panel to its long bottom border and preserve exact name,
Owned quantity, description, and position checks. Account identifiers,
balances, experience, and other game UI are masked. These game-derived crops
are excluded from MIT.

`loot-grid-hard-eleph.png` retains the settled Full List from the September 26
Hard 11-1 receipt. An initial card-layout parse was empty while this modal was
opening; the later saved frame proves all ten cards are readable, including
one Eleph and 1,439 Credits. Runtime tests separately simulate the unsaved
opening transition and require bounded observation without input before
scanning and returning to Sweep Complete. All pixels outside the modal are
masked. This game-derived fixture is excluded from MIT.

`loot-grid-left-tooltip.png` retains the September 26 Hard 8-3 Full List
and its Bluetooth Necklace tooltip. A left-pointing arrow extends beyond the
rectangular text panel; recognition must locate both long side borders before
reading the name. Account identity, balances, and experience are masked.
`loot-cafe-dismiss-animation.png` retains the Cafe Reward Acquired transition
immediately after an item tooltip closes. The cards are still scaling, so the
reader recognizes the receipt but waits for card geometry without another tap.
Only the receipt heading, reward viewport, and continuation hint remain; the
unrelated Cafe HUD and scenery are masked. Both game-derived fixtures are excluded
from the MIT grant described in the third-party notices.
