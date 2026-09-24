# Club check-in — pending the reset test

the job is simple: open Social, enter Club, get the daily attendance AP sent to mail, and return home. it belongs immediately after restart. do it once per game day.

for now, **this is a stub**. it appears in Daily and Cafe plans after restart and reports `deferred`, with zero inputs. the other tasks continue; a completed Cafe visit still updates its schedule. the standalone Club button only records that deferral: it does not launch, tap, or close the game. no attendance checkpoint or important reward action is written.

## reset policy

Nexon lists [Club attendance reset at 19:00 UTC daily](https://forum.nexon.com/bluearchive-en/board_view?board=3221&thread=2121090). that's noon in Los Angeles on September 24, 2026; it becomes 11 am during standard time. `club.game_day()` calculates the occurrence date using UTC, with tests around the boundary, offsets, and year rollover.

once enabled, Daily and the first Cafe visit after reset will run the same daily-gated Club step. later visits that game day should skip it. the existing Cafe timer will remain the only background timer: enabling Club must not schedule Lessons or spend tickets.

## what we've observed

the English 1280×720 staging instance has Social at `(548, 659)`. its overlay has a Club card around `(310, 383)`, alongside Friends and Assistant. Club opens a page with Club, Chat, Member List, and Club ID labels. its home control is at `(1237, 24)`.

the pre-reset inspection reached that page, but did not show a new attendance receipt. the mailbox was empty afterward. that does not establish whether today's attendance had already been collected. the visit is **not** recorded as a new 10 AP reward. raw screenshots remain local under the ignored `work/club/` directory.

## activation checklist

1. After a fresh reset, capture the complete entry flow and any transient notice. Verify the resulting 10 AP mail without claiming it. Identify how to distinguish fresh attendance from an already-visited day.
2. Recognize the actual Social and Club controls with fresh foreground evidence. Bound retries and loading waits; unknown screens, membership prompts, and login interruptions need attention rather than guessed taps.
3. Save receipt evidence and log the important attendance outcome. Keep the visit and verified reward delivery distinct. No chat messages, membership changes, or mail collection belong to this job.
4. Persist the successful occurrence under the configured instance/package and UTC game day, under the instance lock. Reconcile an interrupted attempt before retrying; an uncertain outcome must not create a completed checkpoint. Verify return to an unobstructed home screen.
5. Test same-day repetition, reset rollover, interrupted navigation, and restart recovery. Then replace the stub, prepend restart to standalone Club, mark it as a game job for idle-close handling, and update the dashboard's pending label.

the stub never enables itself at noon. activation requires that live test and a code change.
