# Cafe

The cafe runner completed a live restart → Cafe → home run on the development Mac in **425.5 seconds**, including **379.3 seconds** in the Cafe task. It recognized empty earnings, completed measured scans with boundary checks on both unlocked floors, returned to a verified home screen, and closed Blue Archive through the idle-close setting. This run recorded zero new relationship increases.

Earlier live collection receipts verified **81 AP and 73,957 credits**, then **16 AP and 14,791 credits**. Relationship hearts and rank-up screens were also observed and handled during calibration. The optional invitation flow remains unverified end to end while its cooldown is active.

## Running a visit

```sh
.venv/bin/ba --config config/local.toml cafe
```

Use the dashboard's Run Cafe button for the same sequence. On Windows, replace `.venv/bin/ba` with `.venv/Scripts/python.exe -m ba_automator`. Both `cafe` and `daily` start with restart, so the visit begins from a recognized unobstructed home screen. `--no-downloads` applies to the startup portion.

The routine:

1. Opens Cafe from home and verifies the cafe HUD.
2. Opens Earnings and collects available AP and credits first. A recognized Reward Acquired receipt verifies collection; OCR amounts are retained when readable.
3. Scans for student attention markers and relationship feedback before optional invitations.
4. Pans through overlapping views, measuring scene movement and checking camera boundaries while allowing transient bubbles and animations to settle.
5. Visits both unlocked floors, requiring an unambiguous English switch label before and after each floor change. Unsupported or unrecognized floor layouts stop the routine.
6. Checks the configured optional free invitation after both required scans. One successful invitation ends the invitation check and triggers another scan for the new arrival.
7. Returns to a verified home screen and records the visit result.

A visit has a 15-minute limit. Dialog waits, student attempts per view, and invitation-list scanning are bounded. An unexpected screen or expired frame stops input and leaves diagnostics.

## Student interactions

The detector matches the yellow attention rays using local color and template checks, then selects a point near the student's head inside the room. HUD controls and the floor-switch strip are excluded. It rereads the screen after each action, waits briefly for feedback, and moves the camera to cover more of the room.

An attempted student tap and a verified relationship increase are different records. The latter requires a newly visible relationship-heart template near the target or a recognized rank-up screen. A tap that produces both counts once. Student names are not inferred from appearance. The important-action history can therefore show a floor and a confirmed interaction without a student name.

Yellow rays can disappear after interaction, be covered by speech bubbles, or leave the viewport. A view without markers is not proof that all students were petted. Completing the bounded scan records what was checked and how many relationship increases were verified; it does not claim perfect student coverage. No furniture changes, automatic zoom calibration, or edit-mode difference detection are implemented.

This approach is informed by [ArisuAutoSweeper's cafe recognition](https://github.com/TheFunny/ArisuAutoSweeper/blob/master/tasks/cafe/ui.py) and [BAAH's interaction observations](https://github.com/BlueArchiveArisHelper/BAAH/blob/main/modules/AllTask/InCafe/TouchHead.py). Both use multiple views, and BAAH explicitly accounts for speech bubbles hiding attention markers. Their code and assets are not imported.

## Camera coverage

The scan uses 1,800 ms drags and measures the resulting scene displacement with local feature matching. It works from observed movement instead of a furniture layout template. It first moves to verified camera edges, then traverses overlapping views in alternating directions. Horizontal steps aim for 450 pixels of observed movement and vertical steps for 220 pixels within the approximately 1,010×440 scene interior.

Two separately observed drags with almost no movement establish a camera boundary. If animation or speech bubbles prevent measurement, the runner first retries captures without another drag. If movement remains unknown, it scans that intermediate view for students, resets distance and boundary evidence, and continues with short drags. Unknown movement contributes no distance or boundary evidence. This recovery uses the overlapping movement observed on the fixed display profile. Unexpected movement, twelve unsuccessful drags toward a step/boundary, or exhausted row/column limits stop the scan. The final partial view at each boundary is checked too. The whole visit remains limited to 15 minutes.

Slower pans exposed a previously obscured student marker during live calibration. Measured scans of both floors now pass on the test account; another player's furniture layout also passed a separate camera-movement check. Repeatability across layouts remains under validation. A measured camera scan and confirmed relationship feedback remain separate outcomes. The runner has no zoom dependency.

Upstream [Arisu camera control](https://github.com/TheFunny/ArisuAutoSweeper/blob/master/module/device/control.py) explicitly slows ordinary ADB swipes, and its cafe routine checks overlapping views after camera adjustment. [BAAH](https://github.com/BlueArchiveArisHelper/BAAH/blob/main/modules/AllTask/InCafe/TouchHead.py) also repeats horizontal and diagonal pans with settling time. These are calibration references, not measured guarantees for our BlueStacks input path.

## Gifts and a possible collect-all shortcut

Nexon's [May 26, 2026 patch notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=3443143) describe a gift shortcut: drag a gift onto a student's image in the list at the bottom right. The same update changed the visiting-student list to show relationship ranks. These changes document gifting and student information; they do not establish a bulk relationship-interaction button.

Live inspection of the current Gift panel found gift inventory and student portraits, with no verified head-pat-all control. The runner therefore continues to scan and interact with students individually. Earnings Claim collects stored AP and credits; it does not collect student relationship points. Gift items are not consumed by the cafe routine.

## Free invitations

Invitations are off by default. Configure them in the dashboard, or in `config/local.toml`:

```toml
[cafe]
schedule_enabled = false
invite_enabled = true
invite_student = "Yuuka (Track)"
```

The name must match the English in-game student name, including any variant. The runner checks the current free-invitation cooldown, scans a bounded portion of the MomoTalk-style list, and selects the Invite control belonging to one unambiguous matching name. It handles wrapped variant text, scrolls within the list, requires a contextual normal invitation confirmation, closes the recognized list, and checks for a new cooldown. Missing or ambiguous names, an unrecognized confirmation, purchase-related context, or a request to replace/move a student between cafes stop the flow.

A visible cooldown skips the invitation. A recognized cooldown notice is dismissed and also skips the invitation without claiming success. During current live testing the free invitation was still cooling down, so the available list, exact-name selection, and successful invitation have not yet been verified on this client. Their handlers are based on primary-source UI research and offline tests until the cooldown permits that check.

The paid Bonus Invitation route is not used. The game's current cooldown is authoritative; a locally elapsed timer does not authorize another invitation. Nexon's [Cafe guide](https://forum.nexon.com/bluearchive-en/board_view?board=3222&thread=2523171) documents the **20-hour free-invitation cooldown** and **three-hour student interaction interval**.

## Schedule and controls

Enable scheduling in the dashboard or set `schedule_enabled = true`. Scheduling remains inactive unless `serve` is running. It is independent of whether free invitations are enabled.

A successful cafe or daily job schedules the next visit for three hours and 15 seconds later. Failure sets a retry 15 minutes later. Three consecutive failures pause retries until Resume; this prevents an unknown screen from being retried indefinitely. Existing queued/running cafe or daily work suppresses duplicate scheduled visits.

Pause allows the current job to finish. Stop interrupts it and pauses the queue. Resume clears the scheduler's retry pause and permits work again. Canceling a waiting scheduled visit skips that occurrence and advances its due time; canceling a manual cafe job does not alter the schedule.

`data/state/schedule.json` preserves the due time and retry state across server restarts. The queue itself is not persistent, and there is no operating-system service installed. A server restarted with an enabled overdue schedule may enqueue the due visit.

The optional `[automation] close_app_when_idle = true` setting closes the configured game after the last dashboard job exits and the queue is empty, including a failed or stopped last job. It is off by default. BlueStacks and the dashboard stay running; the next scheduled cafe job restarts the game. Standalone CLI runs are unaffected. Closing the game does not mark a failed visit successful or remove its diagnostics.

## Evidence

Each visit has a run journal, a ring of recent screenshots, and retained reward/relationship evidence. `data/state/important-actions.jsonl` stores attempts and confirmed outcomes across dashboard sessions. The dashboard displays that history separately from debug logs.

A collection attempt is recorded when Claim is pressed; earnings are recorded as collected only after the receipt appears. Relationship taps and verified heart/rank-up feedback are also separate. Invitation requests are recorded before result verification, and a verified new cooldown produces the completion record. A successful overall visit requires the final home screen.

Remaining live validation includes repeat complete visits after the relationship cooldown, obscured markers across more layouts, named free invitations, and interruption recovery. Both-floor scanning, floor transitions, empty earnings, reward receipts, relationship feedback, final home verification, and idle-close have each been observed live. The successful full run used invitations and scheduling disabled.
