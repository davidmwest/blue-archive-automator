# engineering notes

the project automates a small, repetitive part of Blue Archive through its visible interface. the useful engineering problem is knowing when an action is justified, whether it worked, and when to stop. this retrospective covers restart, Cafe, and Lessons; the [high-level design](design.md) sets the forward plan. development uses AI assistance; runtime decisions use the rules described below.

## constraints that shaped the design

the game exposes screenshots and input through ADB, rather than a supported task API. screens arrive asynchronously, announcements change, students animate, and Cafe furniture can be arbitrary. another automator may already share the host's ADB server.

the first version fixes the client to English, 1280×720, and 320 DPI. that makes coordinates and recognition crops reproducible, at the cost of supporting fewer configurations. coordinates are still useful after a screen has been recognized. sending a sequence of clicks without checking the intervening states would make one unexpected popup affect every later action.

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

recognition also needs to distinguish the interface from its artwork. pink hair is not an ownership marker; the portrait border and protruding relationship heart must agree. a completed room keeps its white header, so availability uses the dimmed standard portrait borders. the tests include both cases using sanitized game captures.

one live lesson returned a valid report and reduced the ticket count, then stopped because the grid-close tap ripple briefly covered the school name. the fix waits for the expected name to become readable; it does not loosen identity matching. each spend still needs a matching report, the expected one-ticket decrement, and a completed room. Start is never replayed when a result is uncertain. the failed run also exposed a dashboard bug: its result pointed to the successful restart step. failures now use the final task's journal, keeping the useful evidence attached to the actual failure.

another completed lesson exposed a smaller OCR issue: the overview's visible `4/7` counter disappeared from the full-frame text. the fallback reads only the labeled ticket control at two enlarged scales and requires agreement. a missing count triggers bounded recapture; conflicting reads remain unknown. the actual frame and synthetic counters cover zero through four tickets without treating a missing digit as zero.

## evidence and its limits

[camera tests](../tests/test_cafe_camera.py) exercise arbitrary generated layouts, moving sprites, occlusion, repeated patterns, perspective changes, and misleading fixed backgrounds. [scan tests](../tests/test_cafe_scan.py) check that unknown movement cannot certify an edge or silently skip an intermediate view. screenshot fixtures exercise actual OCR and template recognition; device and server tests cover stale input, protocol mismatches, locks, and serialized jobs.

a documented live run on BlueStacks Air completed restart, measured scans of both unlocked Cafe floors, home verification, and idle closure in 425.5 seconds. it found empty earnings and verified zero new relationship increases. earlier calibration runs verified reward receipts and relationship hearts/rank-up feedback. another player's layout passed a separate camera-movement check. these are observed runs, not a benchmark or a guarantee of finding every obscured student.

that distinction also appears in the product: a tap attempt, a verified relationship increase, and a completed camera scan are separate records. popup attempts retain before/after images. a failed run leaves evidence instead of claiming completion.

the automatic free-invitation path passed a separate live check: a blank target selected Aris (Maid) at relationship rank 26, verified her exact confirmation and new cooldown, logged the result, and returned home. current-star lookup also passed separately. it uses current profile stars at possible cap boundaries rather than assuming the student's original rarity. the complete boundary-rank lookup and reselection, deeper list scrolling, and a configured-name run still have offline coverage only.

live Windows operation and simultaneous operation with ALAS remain unverified. more layouts and repeat visits after cooldown are still useful validation. the [Cafe documentation](cafe.md#free-invitations) records the current boundary between implemented behavior and live evidence.
