# setup and operation

use Python 3.11 or newer and a running BlueStacks instance with ADB enabled. set it to **1280×720 landscape, 320 DPI**, install the global version of Blue Archive, and sign in once with the game language set to English. the automator reuses that login. Cafe currently expects both floors to be unlocked.

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
| `restart` | closes the game and reaches a clear home screen |
| `club` | records “awaiting reset test”; no game input while stubbed |
| `cafe` | runs restart, collects Cafe earnings, and checks students |
| `crafting` | runs restart, checks finished crafts, fills Quick Craft slots, and saves their collection times |
| `lessons` | runs restart, surveys locations, and uses existing tickets with the configured strategy |
| `daily` | runs restart → Club placeholder → Cafe → Lessons; the Lessons step can be disabled |
| `probe` | checks the selected device, game, display size, and foreground app |
| `capture --output data/capture.png` | saves a screenshot without game input |
| `inspect --image data/capture.png` | classifies a saved screenshot without an emulator connection |
| `inspect` | classifies the current game screen without game input |

Club is currently a stub. Its step reports “awaiting reset test” and saves no attendance or reward claim; other steps continue. [The planned check-in flow](club.md) resets at 19:00 UTC.

for example, `.venv/bin/python -m ba_automator --config config/local.toml restart` runs without the dashboard. the installed `ba` command is also available.

required game-data downloads are accepted automatically. add `--no-downloads` to `restart`, `cafe`, `lessons`, or `daily` to stop at a download prompt, or turn off automatic downloads in settings. external sign-in, passwords, 2FA, maintenance, and store app updates stop for attention. after handling the screen, run the task again.

## Cafe and scheduling

Cafe scheduling and invitations are **off by default**. enable them in the dashboard or the `[cafe]` section of your local config. invitations need the exact English name, including variants such as `Yuuka (Track)`. only the free invitation path is supported. its full live flow still needs verification.

collecting Cafe AP clears its storage. spending that AP still needs a mission/sweep job, which isn't built yet.

a successful Cafe job schedules the next visit for three hours and 15 seconds later. failures retry after 15 minutes; three consecutive failures pause retries until you press resume. the schedule survives server restarts. queued jobs don't. no login service or operating-system schedule is installed, so keep `serve` running for scheduled visits.

pause lets the current job finish. stop interrupts it and pauses the queue. resume allows queued and due work to run again. settings changes are rejected while any job is active or queued. press **Ctrl+C** to stop a CLI run or the server.

to close the game between dashboard visits, enable the idle-close setting or set `close_app_when_idle = true` under `[automation]`. it closes the configured Blue Archive app once the queue has finished, including after a failed or stopped final job. it doesn't change standalone CLI runs or shut down BlueStacks. this setting is off by default.

## lessons

queue **lessons** for restart → Lessons, or **daily** for restart → Club placeholder → Cafe → Lessons. the relationship strategy has passed live; [the validation notes](lessons.md#validation-status) cover the tested run and the remaining school rank-up case.

the default is to check every unlocked location, then choose rooms with the most owned students. ties go to the highest sum of owned students' relationship ranks. the alternate strategy picks the lowest school rank, then the lowest XP progress, and chooses its fullest room. tied rooms favor higher owned relationships. it rechecks after every ticket; no ticket purchases are allowed.

these options are in the dashboard's Lessons settings or your local config:

```toml
[lessons]
strategy = "relationship"  # or "school_rank"
max_tickets = 0             # all existing tickets; use 1–99 to limit a visit
locations = []              # empty = all; otherwise exact English location names
enabled_in_daily = true
```

the dashboard's location list takes one name per line. an unknown name stops the job so a typo cannot redirect tickets somewhere else. a configured limit applies per run, not per day. the game counter and already-completed rooms determine what remains available.

`enabled_in_daily = false` removes lessons from the daily plan; the standalone lessons button still works. this does **not** add a daily timer. the existing automatic schedule only runs Cafe every three hours. see [Lessons](lessons.md) for scoring, coverage checks, and receipt verification.

## inspect a run

runs save an `events.jsonl` log and a ring of the latest 24 screenshots under `data/runs/`, plus retained evidence such as `home.png`, popup before/after pairs, Cafe reward receipts, and lesson surveys and receipts. important actions and scheduling state live in `data/state/`. attempted taps and confirmed results are recorded separately.

local config, logs, and full screenshots stay out of Git. use the dashboard to review the latest run; sanitize any evidence before sharing it. [Cafe behavior](cafe.md), [the home map](home-map.md), and [event profiles](event-profiles.md) describe the recognition rules. [the roadmap](roadmap.md) records live validation and what remains unfinished.

## development

run `.venv/bin/python -m pytest -q` for the offline suite. it uses fake devices and reviewed fixtures, with no emulator connection. see [contributing](../CONTRIBUTING.md) for Windows commands and fixture guidance.

## keep crafting

configure Quick Craft in the game first, then enable **keep the crafting slots busy** in the dashboard. it uses available keystones and the displayed credit fee, saves each slot’s finish time, and queues collection/refill visits. if setup is missing, it disables itself with an explanation. [crafting behavior and validation →](crafting.md)
