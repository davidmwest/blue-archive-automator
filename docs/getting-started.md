# setup and operation

use Python 3.11 or newer and a running BlueStacks instance with ADB enabled. start with **2560×1440 landscape, 640 DPI**, install the global version of Blue Archive, and sign in once with the game language set to English. the automator reuses that login. Cafe currently expects both floors to be unlocked.

exact 16:9 landscape resolutions from **1280×720 through 3840×2160** are supported. keep the same effective UI density: 320 DPI at 720p, 480 DPI at 1080p, 640 DPI at 1440p, or 960 DPI at 2160p. pause the queue and let its active job finish before changing the emulator's resolution or density. unsupported dimensions stop the task instead of stretching the interface.

screenshots, receipts, and text OCR use the emulator's native pixels. templates and color checks use a 1280×720 reference; text boxes map back to those coordinates, and targeted text crops retain native detail. taps and swipes scale to the native screen, so existing event profiles keep their coordinates. higher resolution costs more OCR time, rendering, transfer, and storage. the input freshness deadline stays unchanged.

set the frame rate in BlueStacks to suit your machine; the automator leaves it alone. the development setup uses 20 FPS. transitions wait for recognized screens, rather than counting rendered frames.

1440p startup, red-dot scanning, and five Total Assault sweeps have live evidence, with receipt collection recovered after an animation-wait fix. a separate two-floor Cafe scan and return home passed at 1440p and 20 FPS. 1080p and 2160p have offline coverage only; earlier task validation used 720p. [validation details →](engineering.md#evidence-and-its-limits)

paid Google Play checkout is a separate layout: only the observed **720×1280 portrait** checkout is supported. a checkout at another resolution stops for review before payment input. the automator does not change resolution to complete a purchase.

for a dashboard preview without an emulator, use the [read-only demo](../README.md#have-a-look).

## install

clone the repository and run the following commands from its directory.

on macOS:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp config/example.toml config/local.toml
```

on Windows, in PowerShell:

```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
Copy-Item config/example.toml config/local.toml
```

edit `config/local.toml` with the ADB endpoint for the Blue Archive instance and your ADB executable. `adb_path = "adb"` uses your PATH. relative executable and storage paths resolve from the TOML file's directory. the game package is `com.nexon.bluearchive`.

the development setup uses `127.0.0.1:5695` for Blue Archive and `127.0.0.1:5675` for Azur Lane. these are examples, not universal BlueStacks ports. they share the host ADB server on `5037`, with every game command targeting its own instance. use a compatible ADB executable for both. simultaneous operation and Windows still need live smoke tests.

controllers targeting the same game instance must share the same `lock_dir`. leave the example storage paths alone unless you need to move the runtime data.

## start the dashboard

```sh
.venv/bin/python -m ba_automator --config config/local.toml probe
.venv/bin/python -m ba_automator --config config/local.toml serve
```

on Windows, replace `.venv/bin/python` with `.venv/Scripts/python.exe` in these and the following commands.

open [the dashboard](http://127.0.0.1:8765). `serve --port 8767` selects a different dashboard port; this is separate from the emulator's ADB port. the account-free demo defaults to port 8766.

| command | what it does |
| --- | --- |
| `serve` | runs the local dashboard and one-job-at-a-time queue |
| `tasks` | checks the home Tasks red dot and collects completed rewards, including the daily bonus |
| `restart` | closes the game and reaches a clear home screen |
| `club` | checks Social → Club once per game day when notified, then collects mail |
| `cafe` | runs restart, collects Cafe earnings, and checks students |
| `crafting` | runs restart, checks finished crafts, fills Quick Craft slots, and saves their collection times |
| `lessons` | runs restart, surveys locations, and uses existing tickets with the configured strategy |
| `total_assault` | runs restart, tests the configured difficulty in mock battles, and uses tickets only after the team qualifies; see [Total Assault](total-assault.md) for current validation limits |
| `assault_rewards` | checks both raid reward tabs and claims available rewards without entering a battle |
| `tactical_battles` | scouts higher-ranked opponents and fights with the saved team, keeping the configured ticket reserve; then collects Tactical rewards |
| `tactical_rewards` | collects available Tactical Challenge time and daily rewards without spending tickets |
| `daily` | runs restart → Club → free pack → enabled paid packs → mail → Cafe → enabled Bounties → enabled Scrimmages → enabled Tactical battles → Tactical rewards → enabled Lessons → enabled Total Assault → raid rewards → enabled Spend AP → Tasks |
| `scan_ap` | surveys three-star Hard stages and commissions without spending AP |
| `spend_ap` | sweeps the chosen stages without crossing the configured AP floor |
| `probe` | checks the selected device, game, display size, and foreground app |
| `capture --output data/capture.png` | saves a screenshot without game input |
| `inspect --image data/capture.png` | classifies a saved screenshot without an emulator connection |
| `inspect` | classifies the current game screen without game input |

Club checks the Social and Club notification badges, saves a verified visit for the game day, and collects the attendance mail. the attendance notice and actual mailbox receipt are logged separately. [Club attendance](club.md) follows the 19:00 UTC reset.

for example, `.venv/bin/python -m ba_automator --config config/local.toml restart` runs without the dashboard. the installed `ba` command is also available.

required game-data downloads are accepted automatically. add `--no-downloads` to `restart`, `cafe`, `lessons`, or `daily` to stop at a download prompt, or turn off automatic downloads in settings. external sign-in, passwords, 2FA, maintenance, and store app updates stop for attention. after handling the screen, run the task again.

## daily schedule

turn on **settings → daily routine → run daily after reset** to queue the full daily plan once per Global game day. it is off by default. the plan uses your existing inclusion and spending settings; enabling the timer doesn't enable paid packs or Total Assault battles.

```toml
[daily]
schedule_enabled = true
reset_delay_minutes = 1  # 0–120 minutes after the 19:00 UTC Global reset
```

the default run is at **19:01 UTC**, which is noon plus one minute during Pacific daylight time or 11:01 AM during Pacific standard time. the queue shows the next run in your browser's local time. Global reset stays fixed in UTC when your local clocks change.

keep `serve` running and the computer awake. if the server starts late or the computer wakes after reset, it queues the current game day's run; it doesn't replay every missed day. a paused queue waits for resume, and an active job finishes before Daily starts.

the server saves an occurrence before dispatch and records its result, so restarting it won't automatically repeat a completed or interrupted daily run. manually queueing **daily** from the dashboard counts for that game day too. if a run fails or stops, inspect its log and any spending holds, then queue **daily** yourself to retry. cancelling a scheduled Daily skips that game day's automatic run. the next reset permits a fresh occurrence. queued jobs in general still live only in memory; this occurrence record doesn't provide resumable game actions.

## periodic check-ins

**settings → check in between jobs** keeps an eye on red dots and excess AP. it is on by default, every 30 minutes. change the interval from 5 minutes to 24 hours, or turn it off there. the queue shows the last successful check and the next due time in your local time.

```toml
[checkin]
schedule_enabled = true
interval_minutes = 30  # 5–1440 minutes
```

each check opens the game, reads Home and Campaign, and queues the matching reward jobs. excess AP only queues Spend AP when that setting is already enabled, using your current strategy and floor. it doesn't enable paid purchases or repeat the full Daily routine. successful final scans from other jobs restart the interval too, so a busy queue doesn't need a duplicate visit.

the timer survives server restarts. a new timer waits one interval before its first check; an overdue timer catches up once instead of replaying missed checks. check-ins run one at a time with the other jobs, wait while paused, and retry after the next interval if a check fails. the last result stays visible in the queue and the log. keep the server running and the computer awake. the idle-close setting can still close the game after the queue finishes.

## Cafe and scheduling

Cafe scheduling and invitations are **off by default**. enable them in the dashboard or the `[cafe]` section of your local config. leave the invitation name blank to pick the student with the highest relationship below their current cap, or enter an exact English name, including variants such as `Yuuka (Track)`, to pick someone yourself. only normal free invitations are supported; bonus invitations aren't used.

automatic selection checks an empty search field and descending relationship order before choosing. at ranks 10, 20, or 30, it reads the student's current stars from their profile, then returns to the Cafe and checks the list again. rank 100 is already capped. unreadable ranks, names, or filters stop the selection instead of quietly choosing someone lower. [current caps and policy →](design.md#5-configuration-and-game-specific-policy)

the blank-name path has passed live: it selected a rank-26 student, verified the exact confirmation and new cooldown, logged the invitation, and returned home. current-star lookup also passed separately. the full boundary-rank lookup and reselection, deeper list scrolling, and a configured-name run still have offline coverage only. [validation notes →](roadmap.md#2-dashboard-and-cafe--full-visit-and-free-invitation-live-verified)

collecting Cafe AP clears its storage. when automatic AP spending is enabled, a Cafe visit then runs Spend AP using your chosen strategy and AP floor. configure this on the dashboard's **Spend AP** page; see [AP spending](spend-ap.md).

a successful Cafe job schedules the next visit for three hours and 15 seconds later. failures retry after 15 minutes; three consecutive failures pause retries until you press resume. the schedule survives server restarts. queued jobs don't. no login service or operating-system schedule is installed, so keep `serve` running for scheduled visits.

**do everything** resumes the queue and checks what can run. the current batch finishes first, then enabled jobs that are due run in order, followed by a fresh check for red dots and excess AP. when their daily battle settings are enabled, Total Assault and Tactical Challenge also get a catch-up visit even if Daily was already attempted. clicking again while that pass is pending or running doesn't add another pass. your AP floor, spending settings, and unresolved spending holds still apply; the whole Daily plan isn't repeated.

**pause queue** lets the current job finish. open **manual controls** to pick one task, just resume queued work, or **stop now + pause** to interrupt the active job. failed-job notices appear below these controls. settings changes are rejected while any job is active or queued. press **Ctrl+C** to stop a CLI run or the server.

to close the game between dashboard visits, enable the idle-close setting or set `close_app_when_idle = true` under `[automation]`. it closes the configured Blue Archive app once the queue has finished, including after a failed or stopped final job. it doesn't change standalone CLI runs or shut down BlueStacks. this setting is off by default.

## lessons

queue **lessons** for restart → Lessons, or **daily** to include Lessons in the full daily plan shown above. the relationship strategy has passed live; [the validation notes](lessons.md#validation-status) cover the tested run and the remaining school rank-up case.

the default is to check every unlocked location, then choose rooms with the most owned students. ties go to the highest sum of owned students' relationship ranks. it makes one priority list, then groups the selected rooms by school to save time. the alternate strategy picks the lowest school rank, then the lowest XP progress, and chooses its fullest room. tied rooms favor higher owned relationships. the school-rank strategy rechecks after every ticket; no ticket purchases are allowed.

these options are in the dashboard's Lessons settings or your local config:

```toml
[lessons]
strategy = "relationship"  # or "school_rank"
max_tickets = 0             # all existing tickets; use 1–99 to limit a visit
locations = []              # empty = all; otherwise exact English location names
enabled_in_daily = true
```

the dashboard's location list takes one name per line. an unknown name stops the job so a typo cannot redirect tickets somewhere else. a configured limit applies per run, not per day. the game counter and already-completed rooms determine what remains available.

`enabled_in_daily = false` removes lessons from the daily plan; the standalone lessons button still works. the **daily routine** schedule controls when the full plan runs. the Cafe timer leaves lesson tickets alone. Crafting, paid packs, and AP spending have their own optional schedules. see [Lessons](lessons.md) for scoring, coverage checks, and receipt verification.

## total assault

choose a target difficulty and the required winning margin under **settings → total assault**. Hardcore and 30 seconds to spare are the defaults. queue it with the dashboard button or `.venv/bin/python -m ba_automator --config config/local.toml total_assault`. including it in Daily is a separate switch, off by default. the **daily routine** schedule controls when that full plan runs; the Cafe timer does not run Total Assault. [Total Assault](total-assault.md) describes the mock-first policy, assistant fallback, ticket safeguards, and which flows have actually been tested live.

## inspect a run

runs save an `events.jsonl` log and a ring of the latest 24 screenshots under `data/runs/`, plus retained evidence such as `home.png`, popup before/after pairs, Cafe reward receipts, and lesson surveys and receipts. important actions and scheduling state live in `data/state/`. attempted taps and confirmed results are recorded separately.

local config, logs, and full screenshots stay out of Git. use the dashboard to review the latest run; sanitize any evidence before sharing it. [Cafe behavior](cafe.md), [the home map](home-map.md), and [event profiles](event-profiles.md) describe the recognition rules. [the roadmap](roadmap.md) records live validation and what remains unfinished.

## development

run `.venv/bin/python -m pytest -q` for the offline suite. it uses fake devices and reviewed fixtures, with no emulator connection. see [contributing](../CONTRIBUTING.md) for Windows commands and fixture guidance.

## keep crafting

configure Quick Craft in the game first, then enable **keep the crafting slots busy** in the dashboard. it uses available keystones and the displayed credit fee, saves each slot’s finish time, and queues collection/refill visits. if setup is missing, it disables itself with an explanation. [crafting behavior and validation →](crafting.md)

## spend the extra AP

open **Spend AP** in the sidebar. scan your three-star stages, keep the default 100 AP reserve or choose another floor, then pick Elephs, reports, or credits. the Hard rotation supports drag-and-drop, add/remove buttons, arrow controls, and a text editor. enable automatic spending to check hourly and follow Cafe/mail through the same queue. periodic check-ins can send it there sooner when they see extra AP. [configuration, policies, and spending evidence →](spend-ap.md)

### tactical challenge

**settings → tactical challenge** controls ladder battles. Daily and **do everything** include them by default, preserving one of the five daily tickets for manual play. Set the reserve to five to keep all tickets, or turn daily inclusion off. The manual task is under **manual controls → climb tactical challenge**; reward collection stays separate.

**search time per ticket (minutes)** defaults to 10; choose any number from 1 to 30, including fractional minutes. Each ticket gets its own timer. The first 37% establishes a benchmark, then the search gradually relaxes toward the weakest quarter of observed teams. After ten minutes it continues in overtime, relaxing by one observed score tier per unsuccessful refresh until a visible eligible opponent qualifies. A timeout does not consume a ticket or skip its search. Each completed battle starts a fresh timer for the next ticket above your reserve. If the game rejects an opponent, the runner backs out and restarts the search with a full timer, keeping the day's battle history. This is a practical strength heuristic, not a guarantee of finding the weakest opponent. Long searches save elapsed time and overtime progress so other queued jobs get a turn. The TOML setting is `tactical_battles.search_minutes`. Older `refresh_limit` and `confidence_percent` values remain readable for compatibility but no longer control the search.

Verified opponents ahead in rank are ordered by the sum of six student levels, using the player's level for hidden slots, multiplied by 2 for level-90+ players or player level ÷ 90 otherwise. This weighted score is a rough strength estimate with an extra penalty for level-90+ accounts. Each opponent gets at most one battle per game day, regardless of the result. The saved attacking team stays intact; only blank slots are filled. See [Tactical Challenge](tactical-challenge.md) for the search policy, resource guards, and current validation coverage.
