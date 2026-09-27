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

the runner cycles through every unlocked school and checks that the observed ranks add up to the game's Total Area Rank. it repeats the survey before each ticket, including changes to relationship ranks and completed rooms. this takes longer than selecting a batch from one screenshot, but gives every decision a current, inspectable basis. a school allowlist and ticket budget make the scope explicit.

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
dimmed confirmations. this is live survey evidence; a completed native lesson
receipt and a full successful daily are still awaiting verification.

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
returning home without a craft or purchase. these are completed task checks;
the full daily still needs its own successful result.

a later Bounty screen omitted both the ticket arrow and its final digit;
Scrimmage omitted the arrow between two readable digits. the ticket reader
now uses two differently framed native crops to prove the complete change.
both must agree at high confidence with each other and the surviving digits.
Scrimmage's adjacent AP projection remains a separate required observation.
complete contradictory readings still stop the task, and quantity arithmetic
still has to match before any sweep input.

## evidence and its limits

[camera tests](../tests/test_cafe_camera.py) exercise arbitrary generated layouts, moving sprites, occlusion, repeated patterns, perspective changes, and misleading fixed backgrounds. [scan tests](../tests/test_cafe_scan.py) check that unknown movement cannot certify an edge or silently skip an intermediate view. screenshot fixtures exercise actual OCR and template recognition; device and server tests cover stale input, protocol mismatches, locks, and serialized jobs.

the 1440p migration passed live startup through the title screen and announcements to home, plus a red-dot scan. five Total Assault sweeps also completed. their receipt initially arrived while the game was still animating, before Final rewards and Confirm appeared. the collector now waits up to 90 seconds for that final control without replaying the sweep or weakening freshness checks. live recovery logged 500 Total Assault Coins and 50 Advanced Total Assault Coins, verified zero tickets, and returned home. this is recovered live evidence, not a claim that the original run completed uninterrupted. a separate 1440p run at 20 FPS completed measured scans of both Cafe floors and returned home in 468.2 seconds. earnings were empty, and no new relationship increases were verified. this checks navigation and camera coverage at that frame rate; it does not add live high-resolution gift, invitation, or rank-up evidence. 1080p and 2160p have offline coverage only.

the earlier Cafe and completed Lessons validation below used 720p. the native
daily survey described above does not replace completed-lesson evidence.

a documented live run on BlueStacks Air completed restart, measured scans of both unlocked Cafe floors, home verification, and idle closure in 425.5 seconds. it found empty earnings and verified zero new relationship increases. earlier calibration runs verified reward receipts and relationship hearts/rank-up feedback. another player's layout passed a separate camera-movement check. these are observed runs, not a benchmark or a guarantee of finding every obscured student.

that distinction also appears in the product: a tap attempt, a verified relationship increase, and a completed camera scan are separate records. popup attempts retain before/after images. a failed run leaves evidence instead of claiming completion.

the automatic free-invitation path passed a separate live check: a blank target selected Aris (Maid) at relationship rank 26, verified her exact confirmation and new cooldown, logged the result, and returned home. current-star lookup also passed separately. it uses current profile stars at possible cap boundaries rather than assuming the student's original rarity. the complete boundary-rank lookup and reselection, deeper list scrolling, and a configured-name run still have offline coverage only.

live Windows operation and simultaneous operation with ALAS remain unverified. more layouts and repeat visits after cooldown are still useful validation. the [Cafe documentation](cafe.md#free-invitations) records the current boundary between implemented behavior and live evidence.
