# engineering notes

the project automates a small, repetitive part of Blue Archive through its visible interface. the useful engineering problem is knowing when an action is justified, whether it worked, and when to stop. these notes cover the core routines and failures found during live validation; the [high-level design](design.md) sets the forward plan. development uses AI assistance; runtime decisions use the rules described below.

## constraints that shaped the design

the game exposes screenshots and input through ADB, rather than a supported task API. screens arrive asynchronously, announcements change, students animate, and Cafe furniture can be arbitrary. another automator may already share the host's ADB server.

the first version fixed the client to English, 1280×720, and 320 DPI. those coordinates remain the internal reference. the display adapter now accepts exact 16:9 frames from 720p through 2160p and scales input back to the display. templates and color checks use a 1280×720 image; text OCR reads native pixels, including native crops for targeted reads, then maps its boxes back to those coordinates. screenshots keep their original resolution. the recommended setup is 1440p at 640 DPI.

that keeps one set of task coordinates while retaining sharper text and evidence. higher resolution increases rendering, OCR work, screenshot transfer, and storage. a 1440p home-screen OCR pass took about 1.8 seconds on the development Mac. the five-second input deadline still includes capture and recognition; we did not extend it to accommodate slower reads. timing varies by screen and hardware.

coordinates are useful after a screen has been recognized. sending a sequence of clicks without checking the intervening states would make one unexpected popup affect every later action. unsupported geometry is rejected; the separate Google Play payment layout still requires its observed 720×1280 portrait resolution.

local OpenCV and OCR provide the observations. OCR uses a local model; there is no LLM making runtime decisions or cloud inference call. reviewed JSON profiles describe event recognition, although event navigation and farming are not implemented yet. [ALAS](https://github.com/LmeSzinc/AzurLaneAutoScript) informed the overall approach; [ArisuAutoSweeper](https://github.com/TheFunny/ArisuAutoSweeper) and [BAAH](https://github.com/BlueArchiveArisHelper/BAAH) informed Cafe research. more detail is in the [architecture](architecture.md) and [Cafe notes](cafe.md).

## separate the responsibilities

the dashboard dispatches task subprocesses through a FIFO queue. each task holds a per-instance OS lock; controllers targeting the same game must share `lock_dir`. ownership currently ends between tasks, so a separate CLI runner could run between restart and Cafe. uninterrupted plan ownership is a future extension. each job gets a config snapshot, and settings updates are rejected while work is active or queued. the queue is in memory; scheduling state and important-action history persist.

every game command names its ADB device. the transport checks the existing server's protocol before a client operation could disrupt it, and refuses a mismatch without restarting the shared server. taps and swipes recheck their frame deadline after that preflight. startup and Cafe loops have time, repetition, and input bounds.

the dashboard binds to loopback. Host and Origin checks plus a CSRF token protect mutating requests from unrelated browser pages. it is a local controller, with no remote deployment or multi-user authentication claim.

## the camera bug

an early Cafe sweep finished while a clickable student remained. the sweep assumed its planned drags covered the room. live inspection showed that short swipes could produce much less camera movement than expected. counting input commands was inadequate evidence of coverage, and recognizing a particular sofa or floor pattern would fail when the furniture changed.

the current [camera module](../ba_automator/cafe_camera.py) measures displacement from features inside the room, excluding the fixed HUD. it rejects sparse, localized, or competing matches. a constrained affine fallback handles small perspective changes during movement; that fallback cannot certify a stationary camera.

the runner uses slow drags and overlapping views, checking the final partial view at each edge. two separately observed stationary drags establish a boundary. when movement is unknown, it retries captures, checks that intermediate view for students, and resets movement evidence. unknown is never treated as zero. twelve unsuccessful drags toward a step or boundary stop the scan, and the complete Cafe task has a 15-minute limit.

## choosing lessons before spending tickets

Lessons separates observation, selection, and execution. the [planner](../ba_automator/lesson_planner.py) receives school ranks, XP, available rooms, ownership, and relationship ranks as data. the default maximizes owned-student opportunities per ticket; an alternate policy balances the lowest school rank and XP. relationship ranks break ties, and stable identities resolve equal scores. missing evidence stays unknown instead of becoming zero.

the runner cycles through every unlocked school and checks that the observed ranks add up to the game's Total Area Rank. the relationship policy uses each school's All Locations shortcut once, selects the best room set for the ticket budget, and groups visits by school to reduce travel. it verifies the selected room and cost before each Start and updates school progress from the receipt. the school-rank policy still repeats the survey before each ticket because each rank and XP gain can change its next choice. a school allowlist and ticket budget make the scope explicit.

at 1440p, one live survey of 94 rooms took 4 minutes 47 seconds, before returning to the chosen room or verifying its receipt. seven surveys at that pace cannot fit the old fixed 30-minute task limit. the runner now sets its total allowance once, after reading the initial ticket count: `min(90 minutes, max(30 minutes, 2 minutes + 8 minutes × authorized tickets))`. authorized tickets are the smaller of the observed count and `max_tickets`; a configured zero still means all available tickets. seven tickets receive 58 minutes, while zero through three retain 30 minutes. this allows time for the observed work; it does not guarantee completion on a slower machine.

the deadline stays anchored to the original run start, including setup, and never resets after a survey or ticket. an unreadable count cannot extend it, and a count first obtained after the initial 30-minute deadline is too late. later ticket changes still fail reconciliation. the fixed 90-minute ceiling and 2,500-input ceiling remain in force, and expiry leaves remaining tickets unspent. the journal records the count, configured limit, authorized count, and calculated allowance. fake-clock tests complete seven fresh surveys beyond the old deadline and verify the configured ticket limit, original start time, invalid and changed counts, and both ceilings.

recognition also needs to distinguish the interface from its artwork. pink hair is not an ownership marker; the portrait border and protruding relationship heart must agree. a completed room keeps its white header, so availability uses the dimmed standard portrait borders. the tests include both cases using sanitized game captures.

one live lesson returned a valid report and reduced the ticket count, then stopped because the grid-close tap ripple briefly covered the school name. the fix waits for the expected name to become readable; it does not loosen identity matching. each spend still needs a matching report, the expected one-ticket decrement, and a completed room. Start is never replayed when a result is uncertain. the failed run also exposed a dashboard bug: its result pointed to the successful restart step. failures now use the final task's journal, keeping the useful evidence attached to the actual failure.

another completed lesson exposed a smaller OCR issue: the overview's visible `4/7` counter disappeared from the full-frame text. the fallback reads only the labeled ticket control at two enlarged scales and requires agreement. a missing count triggers bounded recapture; conflicting reads remain unknown. the actual frame and synthetic counters cover zero through four tickets without treating a missing digit as zero.

## recovering the first native-resolution daily

the first full daily after the native OCR migration started on schedule but
finished with several recognition failures. sharper pixels changed text
segmentation: stage numbers joined their titles, a padlock joined a boss name,
and overlapping AP digits made a completed sweep look unresolved. targeted
reads now require agreement on complete names or counters, with independent
checks on stage identity, costs, attempts, and tickets. locked Assault rows
need corroborating names across rows; a contradictory valid counter is never
rewritten. Social can use its readable Assistant description when the heading
merges with an icon. Cafe notices still need all three dimmed HUD anchors and
their expected labels. an unreadable value stays unknown.

loading also happens in layers. a Cafe comic may have no loading label, a
Lessons HUD may appear before its room controls respond, and Quick Craft may
show a material counter before the material's icon. the runners now wait for
those observed loading states. Scrimmage waits for its settled list before
reading stars. Lessons and Tasks can retry ignored navigation within explicit
attempt and time limits; Lessons also requires unchanged school, XP, and
tickets. Tasks taps the menu label instead of its illustration. this recovery
never retries a spending input.

the next native Lessons visit completed a survey of 94 rooms across 12 schools
and selected a room with three owned students. it stopped before spending:
the settled confirmation's border blended across a canonical pixel, and
whole-frame OCR omitted part of the `7→6` ticket arrow. recognition now checks
the actual native border and reads the complete cost at two crop scales, with
agreement required against any visible partial digits. room identity, student
ownership and relationship ranks, freshness, and the single-ticket cost remain
separate checks. saved-screen regressions cover the fix and reject moving or
dimmed confirmations. the following native visit completed that first lesson:
three owned students in Hyakkiyako Shopping District, tickets 7 to 6, and a
verified 100 school XP gain. it also logged relationship rank 10 and ATK +23;
the celebration had no written student name, so that identity remains unknown.
the same visit completed two more lessons, reducing tickets to four, before
the next confirmation split `4→3` into the adjacent words `4` and `→3`.
the reader now accepts that complete expression only when all words are
confident, aligned, nonoverlapping, and inside the cost bubble. the fourth
Start input was never sent. the recovery visit then completed all four remaining
lessons and returned home at zero tickets in 1,278.6 seconds. each selection
followed a fresh survey of all 94 rooms. across the two visits at 1440p and
20 FPS, seven distinct rooms provided 17 owned-student opportunities; every
lesson has a receipt, a one-ticket decrement, and a verified 100 school XP gain.
this is recovered completion, not an uninterrupted seven-ticket run.

Tactical Challenge exposed a performance problem: repeated crop reads left
too little time to act on a fresh frame. a cache now reuses level readings only
when all 16 native input crops and the account-level ceiling match exactly.
names, ranks, tickets, hidden slots, and cooldowns are still read afresh. one
saved menu's recognition fell from 4.54 to 2.38 seconds on the development Mac;
the five-second input deadline stayed unchanged. a missing standby clock uses
its labeled crop and never becomes an assumed zero.

loot inspection needed its own loading and geometry fixes. a reward heading
obscured by sparkles gets an exact cropped read; compact quantities require
agreeing reads, including explicit `K` suffixes. Full List ignores clipped
edge cards and scrolls in smaller overlapping steps, preserving ordered
overlap before declaring coverage complete. its time allowance now matches
the 15-minute large-receipt allowance, with the existing input cap. inspection
also waits for fading task banners to leave a stable receipt. missing names
or unproven coverage still appear as incomplete loot.

the interrupted Hard 4-3 sweep was reconciled from its existing reward receipt
and the saved transition from 122 to 102 AP and two to one remaining attempts.
Hard 1-2 had a one-sweep receipt and a verified 365 to 345 AP transition; its
unobserved remaining-attempt count stayed unknown. both recoveries advanced
the rotation without replaying the sweep or duplicating loot. the original
free-pack receipt's completeness was corrected for its 10 AP and 10,000
credits. the five-sweep Bounty receipt grew from 12 to all 22 observed card
quantities and icons, including 150,000 credits; nine names remain unknown, so
it still reports incomplete identification. these were repairs to existing
records while dispatch was paused, not new reward claims.

the recovery also exposed tests writing synthetic command events into the
real daily text log. those tests now isolate their state and log directories.
cleanup removed 264 exact, pinned test records under the log's cross-process
lock, backed up the complete original, and preserved genuine events. no
device commands or other live state came from those test cases. sanitized
captures cover the recognition fixes in regression tests; full evidence and
the repair audit stay local.

the next 1440p recovery visit completed both Cafe floors in 539.9 seconds,
collected 229 AP and 207,081 credits, verified three relationship increases,
and returned home. one settled rank-up showed rank 8 and ATK +78 without a
written student name; that identity remains unknown in the receipt. Quick
Craft also completed its zero-keystone path, preserving the preset and
returning home without a craft or purchase. these are individual task checks;
the later full-Daily verification is recorded below.

a later Bounty screen omitted both the ticket arrow and its final digit;
Scrimmage omitted the arrow between two readable digits. the ticket reader
now uses two scaled native crops to prove the complete change.
both must agree at high confidence with each other and any surviving digits.
a later retry omitted the entire Bounty projection; the same two complete
reads are required when whole-frame OCR finds no digits. the crops exclude
the ticket icon and preserve the full arrow row, so a weak read cannot become
an assumed balance.
Scrimmage's adjacent AP projection remains a separate required observation.
complete contradictory readings still stop the task, and quantity arithmetic
still has to match before any sweep input.

the same recovery completed a Hardcore mock, a real clear with the same team,
and two sweeps. the last receipt contained 200 Total Assault Coins and 20
Advanced Total Assault Coins, but whole-frame OCR omitted both disabled
`0→-` costs afterward. exhausted-ticket recovery requires four agreeing native
reads of those explicit costs, a zero selected count, and no spending controls.
the saved receipt and before/after ticket evidence reconciled the completed
sweep without another input or another loot entry. all three tickets were
accounted for; this was recovery of a completed run, not an uninterrupted pass.

another AP sweep completed before inspection mistook the cyan border of an
Eleph tooltip for a task-progress bar. the detector now requires a filled bar;
an outline cannot start that wait. its saved one-sweep receipt and exact
554-to-534 AP debit advanced the rotation without replaying the spend.
the post-sweep attempt count was not observed and remains unknown. Tasks also
claimed successfully before a background label merged into its reward heading.
that reader now reuses the isolated exact-heading check, retaining the yellow
title and continue-control requirements. sanitized captures cover both cases.
single-item quantities on tall reward cards can also disappear at the usual
crop scale. two larger crops must independently read the same explicit `x1`
(or other quantity) at high confidence before the fallback accepts it. any
conflicting quantity in the original crop remains unresolved.

native receipt inspection also exposed two timing and geometry edge cases.
an exact identity check could finish after its screenshot's five-second input
deadline. the reader now allows three fresh, read-only observations; each must
prove the same receipt before it can authorize input. an expired observation
is never tapped, and a mismatched observation never becomes the next reference.
Full List scrolling could move a card by half a canonical pixel, changing its
detected height by one pixel. ordered multi-card overlap now permits that exact
alignment difference while preserving quantity, tier, column, and artwork
checks. this tolerance does not apply to the identity check before a tap.

the final September 27 Daily ran from 16:39:57 to 17:10:40 PDT at 1440p and
20 FPS. it finished successfully with 14 completed steps, no failed or skipped
steps, and one deferred Tactical Challenge continuation. Cafe completed both
floors; Bounties, Scrimmages, Lessons, and Total Assault confirmed their already
exhausted tickets without spending again. Tactical Challenge completed a battle,
verified the loss and four-to-three ticket change, then saved its next search.
the new portrait-alignment fallback was not exercised in that battle and remains
covered by saved-screen tests only.

Spend AP resumed the previous visit's saved cursor and verified five sweeps,
reducing AP from 492 to 392 with a floor of 100, before returning home and yielding.
one receipt kept ten named items but could not establish page overlap; its loot
record stayed incomplete while independent resource verification allowed Daily
to continue. Tasks collected a Keystone and the final notification scan returned
home. this successful Daily visit does not turn the earlier interrupted ticket
spends into uninterrupted validation or imply that all remaining AP was spent.

the daemon persisted the successful game-day occurrence, dispatched queued
Crafting, and automatically queued AP and Tactical continuations. the resolved
failure notices were then acknowledged without deleting their history. the next
Daily is scheduled for September 28 at 19:01 UTC (12:01 PDT); check-ins and resource
schedules remain enabled. this validates recovery and serial handoff on the
development Mac, not unattended operation across every future screen or season.

## September 28 daily recovery

several independent failures needed different fixes. the shop could finish opening
just after the navigation retry budget expired, so its bounded wait now covers that
transition. native Scrimmage OCR could split the AP projection across two words;
only an exact, high-confidence numeric arrow expression permits the existing
independent ticket-crop verification. expired Lessons inputs are recaptured and
compared before sending a tap, without replaying a sent action.

reward receipts keep their card, quantity, and input-freshness checks. native
heading revalidation tries a focused crop before expensive whole-screen OCR,
so the animation check can finish inside the input deadline. duplicate Tactical
opponent identities cause another observation rather than an uncaught planning
exception. neither change relaxes resource limits or same-day opponent exclusions.

Cafe camera input also recaptures an expired observation, at most three times,
only when ADB confirms that no swipe was sent. movement is measured against the
fresh frame that authorized the successful swipe; unknown movement still cannot
certify a camera edge.

Lesson Report waits inspect its fixed title panel instead of the animated scene
behind it. thin relationship digits may use a third resize scale when one of the
first two reads is absent; two confident readings must agree, and a conflicting
reading remains unknown. room-preview verification still checks every observed
student against the survey before spending a ticket.

native Cafe receipts tolerate tiny heading sparkle changes only with the exact
high-confidence heading text and at least 97.5% overlap of its yellow lettering.
card and quantity checks remain unchanged. Tactical's single-line ticket label
is read left to right so a one-pixel OCR baseline offset cannot reverse the count
and label after a battle.

Total Assault's explicitly recognized season-calculation screen closes the battle
visit successfully. reward checks can still open both reward tabs while preserving
the observed closed-season state. an unread ticket count during an active season
continues to block spending.

relationship cards show a thumbnail cropped from the saved celebration, with a
link to the original screenshot. the game supplies no name on that screen; a
missing name is therefore not an incomplete receipt. unknown ranks and stat
changes are still called out. no portrait classifier or AI is involved.

the September 28 live retry verified the delayed free-package navigation, mail
receipt collection, both remaining Scrimmage sweeps, and a Tactical Challenge win.
the Tactical search then saved its next-ticket progress and yielded to the daily
queue as designed. its later loss was recorded before a ticket-label OCR failure;
a subsequent visit verified one ticket remaining and preserved that manual reserve.
Cafe's full retry completed both floor scans and returned home in 554.6 seconds.
it captured relationship ranks 7 and 11, with ATK +8 and Healing +70 / ATK +4
respectively; both receipts have usable portrait thumbnails without inferred names.
a subsequent uninterrupted Cafe visit completed both floors in 518.2 seconds
and verified a fresh receipt for 14,791 credits and 16 AP, including complete loot
identification. the heading tolerance therefore has both real-frame regression
coverage and a successful live collection. the remaining daily steps are still
being checked.

Lessons' relationship policy subsequently completed the three remaining tickets
from one 94-room, 12-school survey. it visited one Shanhaijing room and two Abydos
rooms, verified each receipt and ticket decrement, and returned home at zero tickets
in 569.1 seconds. the second Abydos room used the first room's observed XP update.
the complete priority list and visit order are retained in the journal.

## evidence and its limits

[camera tests](../tests/test_cafe_camera.py) exercise arbitrary generated layouts, moving sprites, occlusion, repeated patterns, perspective changes, and misleading fixed backgrounds. [scan tests](../tests/test_cafe_scan.py) check that unknown movement cannot certify an edge or silently skip an intermediate view. screenshot fixtures exercise actual OCR and template recognition; device and server tests cover stale input, protocol mismatches, locks, and serialized jobs.

the 1440p migration passed live startup through the title screen and announcements to home, plus a red-dot scan. five Total Assault sweeps also completed. their receipt initially arrived while the game was still animating, before Final rewards and Confirm appeared. the collector now waits up to 90 seconds for that final control without replaying the sweep or weakening freshness checks. live recovery logged 500 Total Assault Coins and 50 Advanced Total Assault Coins, verified zero tickets, and returned home. this is recovered live evidence, not a claim that the original run completed uninterrupted. a separate 1440p run at 20 FPS completed measured scans of both Cafe floors and returned home in 468.2 seconds. earnings were empty, and no new relationship increases were verified. this checks navigation and camera coverage at that frame rate; it does not add live high-resolution gift, invitation, or rank-up evidence. 1080p and 2160p have offline coverage only.

the earlier Cafe validation below used 720p. native Lessons recovery above
verifies all seven starting tickets across an initial three-ticket visit and
a four-ticket recovery visit; the [Lessons notes](lessons.md#validation-status)
record the separate evidence and remaining school-rank validation limits.

a documented live run on BlueStacks Air completed restart, measured scans of both unlocked Cafe floors, home verification, and idle closure in 425.5 seconds. it found empty earnings and verified zero new relationship increases. earlier calibration runs verified reward receipts and relationship hearts/rank-up feedback. another player's layout passed a separate camera-movement check. these are observed runs, not a benchmark or a guarantee of finding every obscured student.

that distinction also appears in the product: a tap attempt, a verified relationship increase, and a completed camera scan are separate records. popup attempts retain before/after images. a failed run leaves evidence instead of claiming completion.

the automatic free-invitation path passed a separate live check: a blank target selected Aris (Maid) at relationship rank 26, verified her exact confirmation and new cooldown, logged the result, and returned home. current-star lookup also passed separately. it uses current profile stars at possible cap boundaries rather than assuming the student's original rarity. the complete boundary-rank lookup and reselection, deeper list scrolling, and a configured-name run still have offline coverage only.

live Windows operation and simultaneous operation with ALAS remain unverified. more layouts and repeat visits after cooldown are still useful validation. the [Cafe documentation](cafe.md#free-invitations) records the current boundary between implemented behavior and live evidence.
