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

`task-rewards-home-ignored-native.png` retains the Tasks icon, label, red dot,
and both Home anchors from the September 27 20:26 UTC failed visit at 2560×1440.
Account identity, balances, and unrelated artwork are masked. All saved frames
remained on Home after three accepted taps on the illustration. This fixture
verifies the label and badge are still recognized; it cannot prove that Android
delivered input or that the revised label target opens Tasks in a live visit.

Home notification samples (`red-dots-*.png`) were captured on 2026-09-24. They mask account identity/balances and private Club details, retaining the fixed badges, Social cards, Free Daily Pack price/confirmation/exhausted state, attendance notice, and receipts. No paid checkout was entered for these captures.

Tactical Challenge reward samples (`tactical-time-ready.png`, `tactical-checked.png`, and `tactical-time-receipt.png`) were captured on 2026-09-24 local time. Account and opponent identities/ranks are masked. They retain an available Time Reward, the disabled controls after collection, and its actual 70,570-credit receipt. The enabled Daily Reward color state is synthesized only inside a test; it is not presented as a live daily-claim capture.

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

`assault-rank-summary.png/json` records the September 27 UTC post-clear
Total Assault Results notice for a clear that did not set a new personal best.
Only the modal is retained; account rank, earned points, season totals, and the
entire background are masked. Its Confirm acknowledges the rank summary and
does not claim items or authorize another ticket. Tests verify the observed
control at (640, 573), missing/dimmed evidence, and bounded one-time dismissal.

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

`loot-grid-scroll-{before,after}.png` retain the Full List modal from the
September 26 Hard 7-1 receipt. Its thirteen items require a short final scroll;
six consecutive cards overlap with slightly different subpixel rasterization,
followed by 1,187 Credits. Tests preserve strict input guards while proving a
unique ordered overlap with matching quantities, tiers, columns, dimensions,
and narrowly bounded artwork differences. Account identity, balances,
experience, and all pixels outside the modal are masked. These game-derived
fixtures are excluded from the MIT grant described in the third-party notices.

`loot-grid-opening-shift-{before,after}.png` retain the Full List modal from
the September 26 Hard 7-1 recovery. Readable cards still move upward two pixels
while the modal opens. Tests require them to settle through OCR before input,
and reject persistent movement, stale captures, or a changed foreground.
Everything outside the modal, including account identity and balances, is
masked. These game-derived fixtures are excluded from the MIT grant described
in the third-party notices.

`tactical-battles-*.png/json` preserve the September 26, 2026 staging opponent
list, opponent detail, saved attack formation with both skip states, and two
formation-expiry notices. Account balances are masked. Player and opponent
names are replaced with synthetic labels; menu club badges are masked. Rank,
ticket projections, public character portraits and tiny unit levels are retained
as recognition evidence. JSON contains the observed OCR boxes with matching
synthetic names. These samples prove navigation and recognition, not victories.
`assets/tactical-hidden-student.png` is the small gray question-mark card used
to distinguish a deliberately concealed student from unreadable visible text.

`tactical-portrait-native-{shift,aligned}-{list,detail}.png` retain only the
public character avatars and a two-pixel canonical registration margin from
two September 27 native 1440p list/preview pairs. Names, ranks, teams, balances,
and every other part of the screen are masked; tests supply synthetic identity
metadata without OCR. One preview displaces the matching avatar down two
canonical pixels, dropping ordinary correlation to 0.639; registration restores
0.986. The earlier aligned pair keeps its original crop. These fixtures prove
portrait recognition only, not a battle or ticket spend, and remain excluded
from the MIT grant described in the third-party notices.

`tactical-battles-opponents-levels.json` records the 16 OCR crop readings for
each visible portrait in the sanitized opponent list. The deterministic replay
reconstructs each source crop locally and requires identical incoming pixels;
this isolates parser and cache tests from CPU-dependent recognition confidence.
Separate OCR integration tests still require the two unambiguous candidates
and validate every accepted level against the observed list. They permit the
third candidate to be omitted when its readings are uncertain, never misread.
`tactical-battles-clipped-eighty-eight-levels.json` records the original macOS
38 interpretation and the taller crops that correctly veto it, so that exact
regression remains covered even when another CPU rejects the early reads first.

`tactical-battles-clipped-eight` preserves a real misreading found during the
first ladder test: the last visible student's level is 80, while tight OCR
crops confidently read 30. Wider crops retaining the complete glyph contradict
that reading, so the opponent is excluded from scoring. This regression guards
against correlated OCR errors rather than treating repeated crops as independent
confirmation. Names and balances are masked as above.

`tactical-battles-clipped-seven` preserves the second audit finding: a visible
level75 was read as15 in the digit crops. Complete labels read75 below the
acceptance threshold; those agreeing contradictions now veto the candidate, and
digit-only fallback also needs a matching full-height reading. The first row is actually
85/75/90. Its names and account balances are masked as above. Uncertain OCR does
not provide a discounted opponent score.

`tactical-battles-clipped-eight-label` preserves a later level-80 card whose
clipped whole label confidently reads `Lv.30`. Wider full-height reads agree
on 80 below the acceptance threshold. Repeated such contradictions now veto
the candidate without authorizing an alternative value. This extends the
earlier digit-crop regression to whole-label errors. Names, club badges, and
account balances are masked or replaced with synthetic labels.

`tactical-battles-clipped-eighty-eight` and `-detail` preserve the September 26
opponent whose last visible level was read as 38 in the list but correctly as
88 in the detail modal. Its actual visible levels are 86/88/88. Taller 18–20px
list crops contradict the apparent 38, so the candidate is excluded rather
than given a guessed score. The detail retains the unchanged 4→3 ticket
projection; no battle was entered. Account balances, names, and badges are
masked or replaced with synthetic labels. Game artwork and recognition pixels
remain covered by the third-party notices, not the MIT grant.

`tactical-battles-refresh-121` preserves an actual post-refresh countdown of
`02:01`. The timer parser accepts that observed value while rejecting larger
counts; live acknowledgement still requires elapsed-time evidence in the
runtime. The fixture has the same privacy masks as the other menu samples.

`tactical-battles-ignored-refresh-before.png` and `-after.png` preserve a
September 26 ignored refresh: the same three opponent rows, own rank 571,
five tickets, and a countdown from 1:51 to 1:48 over 5.192 seconds. The rows
are pixel-identical while strict unit-level recognition excludes every team.
This evidence permits one bounded refresh retry without authorizing a battle
or inferring missing student levels. Names use synthetic labels; account
balances, the player banner, and club badges are masked consistently in both.

`tactical-battles-delayed-entry-campaign` and `tactical-battles-delayed-entry-opponent`
come from the September 26 continuation navigation failure. Campaign remained
visible roughly four seconds after the first input. A repeated input landed on
an opponent as Tactical Challenge opened, leaving its detail modal on screen.
The first fixture removes account balances; the second retains only the modal
with synthetic account names and masked club badges. Its uncertain student-level
OCR does not authorize battle entry. The runtime regression sends one navigation
input and waits for the destination, including a bounded timeout case.

`tactical-battles-live-hud`, `tactical-battles-defeat`, and
`tactical-battles-defeat-tip` retain the first non-skipped battle's timer HUD,
actual loss result, and following effect-type tutorial. HUD player/opponent names
are replaced with synthetic labels. The result and tutorial contain no account
identity or balances. These are live defeat and progress evidence, not a victory.
`assets/tactical-versus.png` is the small fixed VS HUD marker used to distinguish
combat progress from other timers; no combat input target is inferred from it.

`tactical-battles-menu-after-defeat` retains the later menu with four tickets.
The real OCR joined its counter into `Tickets Owned4/5`; the regression permits
missing whitespace without inferring a ticket count. Names, club labels, and
account balances are masked as in the other menu fixtures. The WIN counterpart
test is explicitly synthetic, using the observed result controls with a replaced
title. It does not establish live victory recognition.

`tactical-attack-formation`, `tactical-quick`, `tactical-quick-empty`,
`tactical-quick-restored`, `tactical-quick-special`, `tactical-formation-display`,
and `tactical-formation-sort` preserve September 26 staging observations of the
saved attack team, both roster roles, one temporarily emptied Quick Formation
slot and its restoration, and the filter/sort controls. Account resource bars
are masked; the opponent preview is also masked in the Quick Formation views.
The Display and Sort modals instead retain their complete controls. Paired JSON
contains only OCR words outside the masked areas. These fixtures verify that
existing slots are preserved and that selecting the highest-level available
student requires proven role, filter, sort, and roster position. They do not
establish an automated live fill or victory. The ordinary-formation blank-slot
text in its parser test is synthetic; the Quick Formation empty slot is real.
These game-derived fixtures remain outside the project's MIT grant.

`loot-free-pack-entrance-before.png` and `loot-free-pack-entrance-after.png`
preserve the September 26 Free Daily Pack receipt that delivered 10,000 Credit
Points and 10 AP. The AP card is still scaling into place in the first capture; its final
rectangle is smaller in the second. Initial reward inspection now waits for
stationary cards before establishing its input reference, while the strict
rectangle guard continues to reject the animated-to-settled pair. Only the
reward heading, cards, and continuation hint areas remain; account identity,
balances, and surrounding game content are masked. These are observed receipt fixtures;
the complete recovery flow is tested offline.

`cafe-delayed-visitors.png` preserves the September 26 Visiting Student List
notice that appeared after floor two had briefly become unobstructed. Only the
notice and three Cafe HUD anchors remain; the room, account balances, and other
HUD content are masked. Its paired JSON retains the original notice OCR,
including the optional close icon. The regression accepts that icon only in its
known corner and rejects unrelated dialog content. Delayed dismissal is tested
offline and is not evidence of a completed live recovery.

`tactical-battles-skip-win` preserves the September 26 skipped victory's compact
Battle Result modal, including its WIN! title, reward card, and cyan Confirm
button. Everything outside the modal is masked; no account name, opponent
identity, rank, or balance remains. Paired JSON retains only the modal's OCR.
The live match changed rank 571 to 541 and tickets 5 to 4. This fixture covers
the actual skipped victory layout; the full combat-page WIN test remains
synthetic. Negative tests remove its required anchors or corrupt the title.

`tactical-battles-skip-loss` preserves the September 26 skipped defeat's compact
Battle Result modal. It has a large red LOSE title and a cyan Confirm button,
with no reward row or combat timer. Everything outside the modal is masked;
the paired JSON is fresh OCR of that sanitized image and contains only its
three labels. Recognition requires the observed layout, colors, and exact
large title. Negative tests remove those anchors or alter the title. This
fixture establishes defeat recognition, not a subsequent live recovery.

`tactical-battles-refresh-loading` preserves the September 26 refresh request
whose old opponent list and 01:45 countdown remained readable under the
bottom-right Now Loading overlay. The overlay now prevents all Tactical input
authorization until it disappears. Account resources, player and opponent
identities, profile banners, and the accumulated reward balance are masked;
the paired JSON is fresh OCR of the sanitized frame. The completed-refresh
regression uses the separate observed `tactical-battles-refresh-121` fixture;
it does not claim those two captures belong to one live recovery.

`tactical-battles-ambiguous-preview` preserves the September 26 opponent
preview with visible levels 79, 77, and 79 and a projected ticket change from
2 to 1. Its tiny text produces insufficient evidence for the first level and
conflicting readings for the second. The proven dialog layout now permits a
safe backout while providing no formation-entry target. The surrounding game,
account profile/team, and banners are masked; the opponent name is replaced
with a synthetic label. Paired JSON is fresh OCR of that sanitized image. This
fixture tests recognition and conservative rejection, not a live retry.

`loot-mail-heading-clear.png` and `loot-mail-heading-sparkle.png` preserve a
September 26 mail receipt for three Tactical Challenge Coins. A passing star
makes full-frame OCR read `REWARD AČQUIRED!` in the latter image even though
the reward card remains unchanged. Only the reward heading, card viewport, and
continuation hint remain; account balances and surrounding mailbox content are
masked. Fresh OCR reproduces the exact-heading failure. Runtime regressions
verify bounded read-only reobservation and retain the exact identity and
freshness guards before input. A guarded live recovery also encountered the
same unreadable heading, waited for a clean frame, logged all three coins, and
closed the receipt. These game-derived fixtures remain outside the project's
MIT grant.

`ap-level-up-before.png` and `ap-level-up-receipt.png` preserve the September 26
Hard 11-1 sweep that crossed an account-level threshold. The first frame shows
156/218 AP, a one-sweep projection of 136 AP, and three remaining attempts. The
receipt shows one sweep and 356/220 AP: the projected balance plus the new
220 AP capacity. Its account XP animation still shows level 79 with 14 XP to
the next level. Account name, unrelated balances, and reward identities are
masked. The regression reads both parts of the same unique AP fraction; it
does not infer a level-up from a larger balance alone. These images establish
the observed capacity change, not a subsequent level-up dialog or completed
live recovery. Game-derived fixtures remain outside the project's MIT grant.

`assault-menu-locked-native.png` preserves the September 27 native 2560×1440
difficulty list. Account identity, balances, and unrelated panels are masked;
the paired JSON contains fresh OCR of the sanitized image. Full-frame OCR
joins padlock graphics to the locked boss names. Regressions require matching
native crop readings and a separately observed unlocked-row boss, preserve
the event dates and ticket count, and leave locked rows without input targets.
This fixture verifies offline recognition, not a completed live recovery.
Game-derived fixtures remain outside the project's MIT grant.

`assault-menu-all-locked-native.png` preserves the same September 27 native
list after scrolling to its bottom: Insane, Torment, and Lunatic are all
explicitly locked. Only the title and difficulty panel remain; currency,
account rank, points, and unrelated panels are masked. Its JSON contains fresh
OCR of the sanitized image. Padlocks corrupt two full-frame boss names, but
two native crops of every row independently read the same boss. Regressions
require agreement within and across rows, retain ticket/date checks, and never
expose an input target for a locked row. This is an offline recognition fixture,
not evidence of a completed live survey. Game-derived fixtures remain outside
the project's MIT grant.

`tickets-bounty-native-joined-heading.png` and
`tickets-scrimmage-native-joined-heading.png` preserve September 27 native
2560×1440 Mission Info dialogs, the task heading, and AP needed for projection
checks. Account identity, other balances, and unrelated panels are black.
Native OCR merges each stage index and title; tests still require the numeric
index to agree with the stage letter before recognizing a sweep target.

`tactical-battles-native-missing-clock.png` retains only the September 27 native
standby clock strip and small color guard regions. Account names, opponent
portraits, and other screen content are removed. Its JSON replays the original
full-frame menu OCR with account/opponent identity removed; full-frame OCR
omitted the idle clock. The regression reads the actual native clock crop and
requires an explicit valid clock alongside its label, never an inferred zero.

`crafting-native-material-loading.png` retains the September 27 native Quick
Craft modal with a blank material image and a loading spinner. All account
resources and surrounding content are black. The small matching template
`craft_material_pending.png` contains only the blank card interior. This loading
state must wait without input or disabling the saved preset; only a rendered
keystone can authorize the existing zero-inventory path. These fixtures verify
offline recognition, not completed live recoveries. Game-derived fixtures
remain outside the project's MIT grant.

`ap-hard-native-overlap.png` preserves the September 27 native 2560×1440
Hard 4-3 mission detail after a one-ticket sweep: 102/220 AP and one remaining
attempt. Native full-frame OCR duplicates the final digit of `102` into the
projection `102 → 82`, preventing detail recognition after the successful
receipt. Two separately scaled, padded crop reads must agree exactly; AP,
attempt, quantity, cost, and three-star checks remain in force. Unrelated
account balances and surrounding header controls are masked. This archived
frame, together with the original receipt and persisted intent, establishes
the completed 122→102 AP / 2→1 attempt transition; the fixture itself performs
no state reconciliation or new sweep.

`loading-comic.png` preserves a September 27 Cafe loading illustration with a
black border and centered title; it contains no account identity or balances.
Tests require the title, artwork, and black surround together. This state may
only wait within the existing navigation deadline, never authorize input.
`club-social-native.png` retains the three Social cards at native 2560×1440;
account information and surrounding panels are masked. The assistant icon
merges into its heading in native OCR, so its exact explanatory text supplies
the third card's identity alongside Social, Friends, and Club. Both fixtures
are game-derived and remain outside the project's MIT grant.

`loot-free-pack-native-before.png` and `loot-free-pack-native-sparkle.png`
preserve the September 27 native free-pack receipt for 10,000 Credit Points
and 10 AP. A sparkle causes whole-frame OCR to read `REWARD AGQUIRED!`.
An isolated, generously padded heading crop reads the exact phrase with high
confidence while card names, quantities, and positions remain unchanged.
Only the heading, receipt viewport, and continuation hint are retained; the
account and underlying shop are masked. Tests retain rejection of unreadable
headings, shifted cards, and changed quantities. These archived fixtures verify
recognition without another claim. All three game-derived images are outside
the project's MIT grant.

`cafe-visitors-native.png` preserves the September 27, 2026 native 2560×1440
Visiting Student List notice from a failed daily Cafe visit. Only its Guide title,
list heading, Confirm control, and three dimmed Cafe HUD anchors remain; room,
student portraits, bond levels, balances, and other account content are masked.
After canonical normalization the Comfort label matches the older template at
0.98237, just below the former 0.985 correlation threshold. The regression uses
actual native OCR and permits 0.98 only for this dimmed Comfort anchor; exact
notice labels, title/edit correlations, pixel residuals, and consistent dimming
are still required. Missing or corrupted anchors and unrelated dialog text
remain rejected. This is an offline recognition regression, not proof of a
completed live dismissal.

`loot-bounty-grid-native-before.png` and `loot-bounty-grid-native-after.png`
retain only the September 27 native 2560×1440 Bounty Full List. The former
120px drag moved the shared row into the viewport edge: its cards appeared
85px tall instead of 89px, so overlap correctly failed. The fixtures cover
partial-frame exclusion, directional clipping, and the explicit `x150K`
credit quantity. An offline test reconstructs a 60px intermediate viewport
from these saved pixels to verify overlap and 22-item deduplication; this is
synthetic navigation evidence, not a live short-drag verification. Account
identity, balances, and background screens are blacked out. These game-derived
images remain outside the project's MIT grant.

`tickets-bounty-native-missing-arrow.png` and its sanitized OCR sidecar retain
the September 27 native Desert Railroad H selector at quantity four, with
10 tickets projected to become six. Whole-frame OCR omitted the arrow while
reading both numbers. Recovery requires two independently scaled native crop
reads to prove the same complete projection, then retains the original stage,
quantity, resource, and three-star checks. Currency balances are masked; the
AP and ticket values needed for those guards remain. No ticket is spent by
this offline regression.

`tickets-scrimmage-native-crossfade.png` and `tickets-scrimmage-native-settled.png`
retain consecutive native Trinity list frames from the same daily run. The
crossfade made its headings readable while stars and buttons were translucent,
briefly reporting cleared stages as zero stars. Recognition now waits for the
navy Stage List header to settle. The stable frame still distinguishes the two
three-star stages, the uncleared stage, and the locked stage; inconsistent star
counts during a survey remain an error. Currency balances are masked. These
game-derived fixtures remain outside the project's MIT grant.

`lesson-hyakki-preview-native.png` and its recorded OCR sidecar retain the
September 27 native 2560×1440 Hyakkiyako Shopping District confirmation. Its
settled top edge blends into canonical row87, just beyond the former brightness
cutoff; the two native rows correctly bracket the modal. Whole-frame OCR also
omits part or all of the `7→6` ticket arrow. Two scoped native crop reads must
agree on its complete single-ticket projection without contradicting visible
digits. The fixture preserves room identity and three owned-student hearts
(9, 11, 11), while masking the account header and surrounding scene. Shifted,
dimmed, contradictory, incomplete, and low-confidence variants must remain
unusable for spending. This is offline confirmation-recognition evidence,
not proof of a completed native-resolution lesson. The game-derived fixture
is outside the project's MIT grant.

`lesson-haruhabara-preview-native.png` and its recorded OCR sidecar retain
the September 27 native Haruhabara Multimedia Center confirmation before
the fourth lesson. Full-frame OCR splits its confident `4→3` ticket cost
into `4` and `→3`; scoped crop agreement must corroborate both visible
digits. Only the Location Info modal and its original OCR remain; the
account header and surrounding scene are masked. The fixture tests safe
confirmation recognition, not a fourth ticket spend. The game-derived
fixture is outside the project's MIT grant.

The native Lessons sidecar includes absolute source pixel boxes and output
shapes for every recorded crop. Replay resizes those exact fixture regions on
the current OpenCV build, since cubic interpolation can round differently
between platforms. Changed incoming pixels still receive no recorded result;
the original capture hashes remain available as provenance.

`loot-sweep-task-progress-native-refresh-before.png` and `...-after.png`
retain the September 27 native Hard 3-2 sweep receipt with a partial cyan
achievement-progress banner, then the same receipt after it disappears.
The earlier detector only waited for gold completion banners. These fixtures
verify that a partly filled cyan bar also triggers a bounded wait without
input; the uncovered heading, close control, complete Final row, quantities,
and Confirm button remain exact before the clean receipt can be used.
Account identity, balances, and surrounding scenes are masked. These are
saved observation frames, not proof of a retried live collection. The
game-derived fixtures remain outside the project's MIT grant.

`loot-native-amulet-quantity.png` retains only the native Final-row Traffic
Safety Amulet card from the September 27 Hard 2-1 receipt. Its Japanese
artwork lettering overwhelms both the whole-card and wide bottom-strip OCR;
two independent lower-right quantity crops at 4× and 5× must instead agree
on the complete, confident `x1` label. Everything outside that card is masked.
This tests earned quantity recognition, not item-name inference or inventory
totals. The game-derived fixture remains outside the project's MIT grant.

`tickets-bounty-native-missing-final-digit.png` and
`tickets-scrimmage-native-missing-arrow.png`, with recorded native OCR sidecars,
retain September 27 native 2560×1440 quantity selectors. Classroom H shows four
sweeps and `5→1` tickets, but whole-frame OCR only retains the first `5`.
Gehenna B shows two sweeps with an intact `574→574` AP projection and separate
`10` and `8` ticket digits. A wide native crop and a tighter enlarged crop must
both read the complete ticket projection confidently, agree with every surviving
ticket digit, and pass the original quantity and independent AP arithmetic.
Currency balances are masked; AP remains to exercise the header equality guard.
These saved selector frames prove recognition only, not any completed sweep.
The game-derived fixtures remain outside the project's MIT grant.

`loot-sweep-task-fading-native-{visible,fading,clear}.png` preserves the native
1440p heading/control and Final-row pixels from the September 27 Hard 1-2
one-sweep receipt (`spend_ap-20260927T201412-34498b04`, receipt 0122). All pixels
outside those recognition regions, including account identifiers and balances,
are blacked out. The fading frame no longer has a detectable cyan progress bar,
but it still changes the heading: the strict receipt guard must reject it while
the bounded, input-free capture wait allows the animation to finish. These are
saved real frames; the fixture does not establish any additional resource spend.
The fading frame also starts an integrated optional-inspection recovery test:
its narrowly detected top-margin shadow must clear before a clean reference is
saved, with title, controls, Final-row pixels, task and sweep count preserved.
The original evidence and pending resource intent remain unchanged. OCR alone
is stubbed in that replay because the sanitized crops omit the Final label.

`assault-zero-detail-native.png` and its recorded whole-frame OCR retain the
September 27 native Hardcore Room Info screen after the final two-ticket sweep.
Whole-frame OCR reads the zero sweep count but misses both disabled `0→-`
ticket costs. Four tightly scoped native crop reads must independently agree
on the explicit zero/dash notation before the exhausted budget is recognized;
missing, contradictory, or low-confidence evidence stays unresolved. The modal
and event period remain; account balances and the surrounding scene are masked.
The fix exposes no spending controls and tests final ticket reconciliation,
not permission to repeat the sweep. The game-derived fixture remains outside
the project's MIT grant.

`loot-native-eleph-tooltip-not-task-notice.png` retains the cyan-bordered
Izumi Eleph tooltip from the September 27 native Hard 13-3 receipt
(`spend_ap-20260927T214623-99fafb92`, receipt 0046). Its outline crosses the
same strip as the task-progress bar, but only 13% of its bounding rectangle
is cyan. Recognition must require a filled progress bar, so an intentionally
opened tooltip reaches ordinary item inspection without an impossible wait.
Only the tooltip and the notice detector regions remain; account details,
balances, and the surrounding receipt are masked. This saved frame tests
recognition, not permission for another sweep. The game-derived fixture
remains outside the project’s MIT grant.

`task-rewards-receipt-merged-native.png` and its recorded OCR retain the
September 27 native Tasks receipt whose gold heading merged with background
task text as `REWARD ACQUIRED!k(s)`. The fallback must read the exact isolated
heading while retaining the yellow-title and Touch to Continue checks. The
image keeps only the heading, visible reward cards, and continue control;
account information and the surrounding scene are masked. This fixture proves
receipt recognition, not complete coverage of the reward list. The game-derived
fixture remains outside the project's MIT grant.

`tickets-bounty-native-missing-projection.png` and
`tickets-scrimmage-native-weak-projection.png`, with their recorded OCR, retain
the September 27 Classroom H and Gehenna B sweep confirmations. Whole-frame
Bounty OCR omitted all of `5→2`; the Scrimmage crop read `10→6` below the
required confidence. Two complete native crop readings must independently
agree on the projection and all surviving digits. Stage, stars, quantity,
unchanged AP, and active controls remain separate checks. Tests reject partial,
contradictory, and low-confidence counters. The fixtures mask the surrounding
scene and retain only recognition regions; they authorize no additional sweep.
Game-derived fixtures remain outside the project's MIT grant.

`loot-bounty-grid-half-pixel-{before,after}.png` preserves the two native
Full List pages from the September 27 Classroom H five-sweep receipt
(`bounties-20260927T223039-9157ec6c`, receipt 2). After a 51px upward movement,
the same six cards have normalized contour heights of 89px and 88px. A fixed
half-pixel center alignment must prove their ordered overlap without relaxing
strict input identity or changing any observed quantity. Heading and quantity
OCR are recorded in the lightweight replay; card discovery, silhouettes, icons,
and overlap comparison use the real saved pixels. Both pages still have a
clipped bottom row, so this fixture does not prove complete receipt coverage.
Only the Full List modal remains; surrounding account details and balances are
masked. The game-derived fixtures remain outside the project's MIT grant.

`tickets-scrimmage-native-zero-fragment.png` and its recorded OCR retain the
September 27 Millennium B detail after the final five Scrimmage tickets were
spent (`scrimmages-20260927T231227-fb8e45c6`). Native whole-frame OCR retained
only the initial zero of `0→-`; the pale bubble border reduced confidence in
both enlarged readings. The projection crop excludes that border, retains the
full digits/arrow/dash, and still requires two complete, confident observations.
AP, stage, zero quantity, and no-input exhaustion guards remain independent.
Unrelated account balances are masked. This fixture proves the already-spent
balance, not permission to repeat the sweep. The game-derived fixture remains
outside the project's MIT grant.

### Cafe reward heading animation (September 28, 2026)

`loot-cafe-title-read.png` and `loot-cafe-title-refresh.png` retain only the
1440p reward heading and card row. The account HUD and surrounding Cafe are
masked. Both show the same 14,791 credits and 16 AP, while a passing sparkle
changes OCR's bounding rectangle for the slanted title. Tests require the exact
confident heading and fixed lettering as well as unchanged card identities;
a moved heading or altered quantity is rejected. These game-derived fixtures
remain outside the project's MIT grant.

### September 29 daily-job regressions

- `assault-rewards-offseason.png/json` preserve the native 1440p closed-season
  status, next-season time, Total Assault heading, and Rewards control. Account
  balances and identifying account/rank areas are masked.
- `lesson-trinity-rank-one-native.png` captures the Trinity room grid whose thin
  relationship-rank 1 was missed by the earlier crops. Account balances are masked.
- `loot-event-grid-scroll-before.png` and `loot-event-grid-scroll-after.png` retain
  only the Full List panel from the 43-sweep event receipt. They exercise fractional
  scroll antialiasing and six overlapping blueprint cards; everything outside the
  panel is masked. These fixtures establish page matching, not complete itemization
  of the historical receipt.

These game-derived images remain outside the project's MIT grant.

`lesson-trinity-rank-one-confirm-native.png` is the September 29 confirmation
popup for the same Central Library room. Its top currency strip is masked. It
checks that the grid and confirmation both preserve the owned rank-1 student.

`drill-expired-mock.png/json` capture the expired free practice room after the
failed September 29 daily visit. The top currency strip is masked. Its partial
score and two unfinished formations remain a mock settlement, never paid reward
evidence. This game-derived image also remains outside the project's MIT grant.

`drill-assistant-fee-native.png/json` preserve the September 29 assistant fee
modal at native 1440p with the top account balances masked. The portrait background
breaks the full star-badge template, exercising two agreeing close-up rarity
reads before confirming the exact qualified assistant and 40,000-credit fee.
This game-derived image remains outside the project's MIT grant.

`cafe-neighbor-invitation-words.json` contains only OCR text and coordinates
from the September 29 neighboring-Cafe transfer warning. It excludes account
balances and the background student list. The regression verifies cancellation
and continuation without confirming a transfer or spending an invitation.

`packs-weekly-packs.png`, `packs-weekly-ap.png`, and
`packs-weekly-ap-confirm.png` are September 30, 2026 BlueStacks Air 1440p
captures of the named weekly offers and AP purchase confirmation. Account
identity, currencies, and the surrounding home screen are masked. They verify
exact titles, USD prices, and weekly stock before permitting a purchase.
`packs-weekly-ap-payment-sanitized.png` preserves only Google Play's product,
price, app title, heading, and buy-button labels; account and payment details
are removed. It tests the 720p recognition layout and its observed 1440p scale.
These game/store-derived images remain outside the project's MIT grant.

`packs-weekly-reports-payment-sanitized.png` preserves only the September 30
Lite report checkout's product/price, app title, Google Play heading, and buy
button. Account, payment, and Play Points details are removed. It covers the
shorter Play product label and OCR joining the adjacent title and price.

`task-rewards-expert-permit-split-native.png/json` preserve the September 30
Expert Permit receipt with the top account strip masked. The native image
exercises an isolated reward heading split into two OCR words, while the saved
full-frame OCR reproduces background text merging into the title.

`loot-event-sweep-opening-before.png` and `loot-event-sweep-opening-after.png`
preserve the September 30 event Quest 5 receipt's opening and settled layouts.
Account identity, balances, and experience are masked. They verify that the
Final-row animation settles before establishing the reference for Full List
inspection and that incomplete inspection keeps that proven reference. These
game-derived images remain outside the project's MIT grant.

`loot-event-grid-equal-height-before.png` and
`loot-event-grid-equal-height-after.png` retain only the Full List modal from
the September 30 Quest 5 sweep receipt. Account identity and balances outside
the modal are masked. They verify six repeated equipment cards across a
16-pixel scroll, preserving the final activity report and 514 credits without
double counting. These game-derived images remain outside the MIT grant.

`loot-treasure-single-credits-native.png` preserves the September 30 treasure
tile's centered Credit Points ×40,000 reward receipt at native 1440p. The top
account bar is masked. It exercises a complete, stable, single-card receipt
whose high-confidence title and quantity can be recorded without opening a
tooltip or scrolling. This game-derived image remains outside the MIT grant.

`loot-treasure-carousel-qualifier-before.png` and
`loot-treasure-carousel-qualifier-after.png` preserve September 30 treasure
prize carousel pages 3 and 4, with the account bar masked. They reproduce OCR
dropping the space before `(Hyakkiyako)` after a scroll. The regression requires
the unique three-card overlap and leaves the partially visible final doll for
the next page. These game-derived images remain outside the MIT grant.

`drill-owned-restoration.png/.json` captures the owned Quick Formation roster with
one explicitly empty slot, selected owned cards, and an available Aris (Maid).
The account header is masked and removed from OCR. This tests exact variant names,
selected-card exclusion, and recovery of a saved formation before free mock
qualification; it does not establish a live-tested restored Drill victory.

`lesson-cafeteria-header-native.png/.json` preserves only the native Millennium
School Cafeteria room header and its recorded OCR. Both enlarged reads duplicated
the end of “School” into an overlapping “I Cafeteria” box, despite correct
full-frame OCR. This regression rejects the corrupt crop override while keeping
the exact preview-name and ticket-cost checks. No account data is included; the
game-derived image remains outside the MIT grant.

`loot-single-credit-reference-native.png`, `loot-single-credit-sparkle-native.png`,
and `loot-single-credit-settled-native.png` retain only the September 30 single
Credit Points ×40,000 receipt heading and card. A passing heading sparkle
changes OCR boxes enough to fail strict receipt identity for one frame. The
regression waits for another read against the original reference, without
relaxing identity checks or sending input. All other pixels are masked; these
game-derived images remain outside the MIT grant.

`loot-event-grid-large-sweep-before.png` and `loot-event-grid-large-sweep-after.png`
retain only the Full List modal from a September 30 event Quest 9 ×44 sweep.
Six equipment cards move upward by 32 pixels with identical columns, quantities,
and artwork; fractional rendering adds minor antialiasing differences. Tests
prove the six-card overlap without double counting and reject changed amounts,
artwork, alpha masks, tiers, columns, and ordering. The second page still has
more rewards below it, so these fixtures do not establish a complete receipt.
All account background is masked; these game-derived images remain outside the
MIT grant.

`assault-assistant-native-metadata.png` preserves only six native-resolution
level and weapon-star badge crops from the September 30 paid-entry assistant
verification. Tall-strip OCR omitted Hina (Dress)'s weapon-star digit and another
card's level; independent agreeing digit reads recover the exact metadata.
All student portraits, names, lenders, and account content are masked. The
game-derived badge pixels remain outside the MIT grant.

`loot-drill-title-native-before.png` and `loot-drill-title-native-after.png`
retain only the heading and two reward cards from a completed September 30
three-round Joint Firing Drill. Canonical title OCR merges passing sparkles;
native/enlarged isolated reads preserve the exact phrase. Tests retain strict
card quantities and title placement. Account content is masked; game-derived
pixels remain outside the MIT grant.

`treasure/entry-dialogue` preserves the entry speech bubble crossing the board;
account currency and AP headers are masked. It guards against treating the
bubble's translucent white panel as revealed empty stone.
