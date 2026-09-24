# Lessons

Lessons is the third game task, following restart and Cafe. Its first version supports the global English client at 1280×720 and 320 DPI. This document defines its policy and verification contract; the validation section records the observed results and remaining limits.

## What a visit does

1. Begin at a verified unobstructed home screen and open Lessons.
2. Read available tickets and inspect every unlocked location in the configured scope (all locations by default). Record location identity, rank and XP when needed, and the available rooms with their visible students.
3. Compare eligible rooms according to the configured policy. Keep observations separate from the pure planner so selection can be tested without the game.
4. Reopen and verify the selected room. Confirm one lesson using one existing ticket.
5. Verify the result and the reduced ticket count, record the evidence, and refresh the relevant observations before planning the next lesson.
6. Finish when the ticket budget is spent or no eligible useful room remains, then return to a verified home screen.

The routine does not buy lesson tickets. A room that has already completed its lesson is excluded. Unknown or incomplete school coverage stops spending; an early page with a good room does not establish the best room in the configured scope.

## Relationship policy — default

The objective is to maximize **owned-student relationship opportunities** with the available tickets. The planner compares eligible rooms across all locations in scope, using:

1. Most owned students.
2. Highest sum of those owned students' visible relationship ranks.
3. A stable location/room identity tie-break so repeated observations yield the same choice.

Unowned students do not count toward relationship gains. A room with three students and two owned students loses to a room with three owned students. Relationship ranks only break a tie in owned-student count; a single high-rank student does not outrank two owned students.

If every available room has zero owned students, this policy leaves the remaining tickets unused. Missing ownership evidence is not zero ownership. Missing relationship ranks block a decision only when the unresolved values could change a tied comparison.

This policy optimizes owned-student count, rather than estimating exact relationship points. It does not weight location reward levels, random bonuses, or student relationship caps. The dashboard distinguishes a planned opportunity, an attempted lesson, and rewards verified from the game.

## School-rank policy

This policy follows the requested progression rule: raise the lowest-ranked location, including its XP progress, rather than repeatedly training an already-developed location.

1. Consider locations below their observed maximum rank that have eligible rooms.
2. Choose the lowest rank, then the lowest fractional progress toward its next rank. For example, rank 5 at 100/500 XP precedes rank 5 at 300/500 XP; any rank 5 location precedes rank 6.
3. Among locations tied at that rank and XP progress, choose the room with the most students. For tied student counts, prefer the highest sum of owned-student relationship ranks, then the stable location/room identity.
4. Use one ticket and reread rank and XP before choosing again. If the location is no longer the lowest, switch to the new lowest location.

Here, room population means all visible students; relationship opportunities use owned students only. When every eligible location is at maximum rank, fall back to the relationship policy.

Balancing rank progress is a preference, not a mathematical claim that this always produces the next increase in aggregate rank with the fewest tickets. Daily ticket capacity grows at aggregate-rank thresholds, rather than equaling the sum of all location ranks. The observed ticket counter is authoritative for the current visit.

## Observation and action rules

The runner recognizes controls and values from fresh screenshots with local image processing and OCR. Student artwork is not used to infer ownership. Locked locations, completed rooms, names, ticket counts, ownership markers, and rank/XP displays each need explicit observed evidence.

The location survey is bounded and checks for repeated pages and duplicate locations. An unknown screen, inconsistent ticket count, incomplete survey, ambiguous room confirmation, or exhausted timeout stops the run with evidence. A frame used for input must still be fresh after recognition and persistence, and Blue Archive must remain the foreground package.

Every lesson has a before/after record with the location, room, selected policy, score, tickets before/after, and any readable reward or rank change. A confirmation tap records an attempt. A verified lesson records completion. Relationship increases are recorded only when the result screen supports them; school XP or a ticket decrement alone does not prove a relationship reward.

The shared `runtime.py` provides a bounded screenshot ring, journal, task error, result, and capture timestamps. The task uses the same queue and instance lock as restart and Cafe. It does not create a second device worker or make runtime AI calls.

### Recognition details and rank-up limits

The room grid must finish its opening animation before the fixed portrait coordinates can be used. Ownership requires a pink border and a relationship heart protruding beyond the portrait; red or pink hair inside a blue border is unowned. Small relationship numbers use enlarged local crops. Long room names use agreeing reads with a white crop margin, and OCR fragments on one line remain in left-to-right order. A missing ticket count uses agreeing enlarged reads of its labeled control. These crops are cached; the runner recaptures when recognition leaves less than one second for evidence persistence and device preflight within the five-second input deadline.

Completed rooms retain white headings while every portrait's standard border becomes dim. The detector checks that consistent dimming, rather than student artwork or green check marks alone. Lesson Report recognition requires the loaded location, relationship, and reward sections with one visible Confirm control. Its blank loading state is not a receipt.

The relationship rank-up screen was captured during the first live lesson and has a replay fixture. A separate school/area rank-up can occur when location XP crosses a threshold. The conservative `area_rank_up` handler requires an exact **Area Rank Up** or **Location Rank Up** heading and one visibly active cyan **Confirm** control inside the modal. It never invents a tap-anywhere dismissal.

That school-rank handler has **synthetic contract tests only**. A current English screenshot and localization entry establishing its actual title/layout were not found, and the development account has not yet triggered it live. An unrecognized heading or tap-anywhere variant stops with evidence. [BAAS's lesson flow](https://github.com/pur1fying/blue_archive_auto_script/blob/4d9e25ff8cce018dee7ffec44e564fdc9f149b60/module/lesson.py) confirms a separate area-rank interruption exists; its implementation and assets are not imported. A captured live rank-up remains a required validation case for unattended progression through rank changes.

## Configuration and scheduling

The dashboard and local TOML configuration expose the same settings:

```toml
[lessons]
strategy = "relationship"  # or "school_rank"
max_tickets = 0            # 0 = all existing tickets; otherwise 1–99 per visit
locations = []             # all locations, or exact English in-game location names
enabled_in_daily = true
```

An explicit location list restricts optimization to those locations. An unknown configured name is an error rather than a silent fallback to all locations. `max_tickets` limits the current visit; it never authorizes purchasing tickets or exceeding the observed counter.

Lessons can run as its own queued job or as the Lessons step of a daily plan. The default daily plan is restart → Club placeholder → Cafe → Lessons; disabling `enabled_in_daily` removes only that final step. A standalone Lessons visit runs restart → Lessons. The Cafe job is restart → Club placeholder → Cafe, so its three-hour automatic schedule does not enqueue Lessons.

Including Lessons in the daily plan does not itself install a daily schedule: the existing automatic timer is the Cafe cooldown schedule.

Daily ticket availability is read from the game. A later daily scheduler must identify the server reset and persist the occurrence key; a fixed interval measured from the previous lesson is not equivalent to a server day.

## Validation status

On September 24, 2026 UTC, the authorized BlueStacks Air staging instance completed the following checks:

- Surveys covered all 12 unlocked locations and 94 room cards, reconciling to Total Area Rank 80 before each ticket.
- All seven available tickets were used on seven distinct rooms with two owned students each, for 14 owned-student lesson opportunities. Every ticket has a matching report and an observed one-ticket decrement; each selected school gained 100 XP.
- This total includes calibration and stopped runs. The final uninterrupted dashboard job consumed the remaining two tickets, repeated the full survey between them, reached zero tickets, returned to a clear home screen, and closed the app. Lessons took 419.8 seconds; the complete job including restart took 475.3 seconds.
- Relationship rank-up interruptions, completed-room exclusion, wrapped room names, the small ticket counter, transient tap effects, and slow recognition were exercised. Captured regressions and timing tests cover the issues found during calibration. A rejected stale tap sent no input and left its ticket available.
- The 593-test offline suite passes on macOS, Windows, and Ubuntu. Wheel/source packaging, included fixtures, license notices, and the dependency-free installed demo pass their checks.

The final local run is `lessons-20260924T064611-02b9e014`. Full screenshots, survey JSON, receipt evidence, and important-action history remain local; the repository contains sanitized replay fixtures.

All live spending used the relationship policy. The school-rank policy is covered by observed-data and multi-ticket rank/XP tests, including switching schools after each ticket, but a real school rank-up popup remains unverified as described above. Live Windows execution and broader account/layout coverage remain future checks.

## Mechanics references

Nexon's [Lessons guide](https://forum.nexon.com/bluearchiveTW/board_view?allBoard=1&board=3354&thread=2482450) explains ownership indicators, location progression, and daily lesson availability. The [May 26, 2026 update notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=3443143) describe the lesson location EXP display control. These references inform recognition; the current game screen determines whether a room or ticket is available.
