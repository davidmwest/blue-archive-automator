# blue archive automator

we're making a custom blue archive automator. it should do the daily stuff, use a reasonable amount of cpu, and run alongside the azur lane automator without getting in its way.

we're using bluestacks + ADB, with [ALAS](https://github.com/LmeSzinc/AzurLaneAutoScript) as the model: recognize the screen, do one thing, check what happened. fixed resolution, Python, OpenCV, and local OCR. there's no cloud AI in the loop.

## what it does

`restart` closes blue archive, opens it again, gets through the title screen, accepts required game-data downloads, closes startup popups, and waits for a clear home screen. that path has passed a live run on this mac.

`cafe` starts with `restart`, collects available AP and credits, then checks students across both unlocked cafe floors. free invitations are optional and need an exact student name. `daily` currently runs the same restart → cafe sequence. cafe is implemented and still being tested against the live game; a completed scan doesn't mean every student was successfully petted.

there's a local dashboard with a serial job queue, stop/pause controls, settings, a home-screen map, popup before/after screenshots, and a persistent log of important actions. an optional cafe schedule runs every three hours plus 15 seconds while the server is open.

you can also have it close blue archive after the last dashboard job finishes. the emulator stays open, and the next cafe visit starts with restart again. this option is off by default.

## setup

use Python 3.11 or newer and a running bluestacks instance with ADB enabled. set it to **1280×720 landscape, 320 DPI**, install the global version of blue archive, and sign in once with the game language set to English. the automator reuses that login.

on mac:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp config/example.toml config/local.toml
```

on windows, in PowerShell:

```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
Copy-Item config/example.toml config/local.toml
```

edit `config/local.toml` with the ADB endpoint for the blue archive instance and your ADB executable. `adb_path = "adb"` uses your PATH. relative executable and storage paths are resolved from the TOML file's directory. the game package is `com.nexon.bluearchive`.

this mac's blue archive instance uses `127.0.0.1:5695`. azur lane uses `127.0.0.1:5675`. they share the host ADB server on `5037`, with every game command targeting its own instance. use a compatible ADB executable for both. simultaneous operation and windows still need live smoke tests.

## run it

```sh
.venv/bin/ba --config config/local.toml probe
.venv/bin/ba --config config/local.toml serve
```

open [the dashboard](http://127.0.0.1:8765). `serve --port 8766` selects a different dashboard port; this is separate from the emulator's ADB port.

| command | what it does |
| --- | --- |
| `serve` | runs the local dashboard and one-job-at-a-time queue |
| `restart` | closes the game and reaches a clear home screen |
| `cafe` | runs restart, collects cafe earnings, and checks students |
| `daily` | runs restart, then cafe |
| `probe` | checks the selected device, game, display size, and foreground app |
| `capture --output data/capture.png` | saves a screenshot without game input |
| `inspect --image data/capture.png` | classifies a saved screenshot without an emulator connection |
| `inspect` | classifies the current game screen without game input |

for example, `.venv/bin/ba --config config/local.toml restart` runs without the dashboard. on windows, use `.venv/Scripts/python.exe -m ba_automator` instead of `.venv/bin/ba`. `.venv/bin/python -m ba_automator` also works on mac.

required game-data downloads are accepted automatically. add `--no-downloads` to `restart`, `cafe`, or `daily` to stop at a download prompt, or turn off automatic downloads in settings. external sign-in, passwords, 2FA, maintenance, and store app updates stop for attention. after handling the screen, run the task again.

## cafe and scheduling

cafe scheduling and invitations are **off by default**. enable them in the dashboard or the `[cafe]` section of your local config. invitations need the exact English name, including variants such as `Yuuka (Track)`. only the free invitation path is supported; the paid bonus invitation isn't used.

a successful cafe job schedules the next visit for three hours and 15 seconds later. failures retry after 15 minutes; three consecutive failures pause retries until you press resume. the schedule survives server restarts. queued jobs don't. no login service or operating-system schedule is installed, so keep `serve` running for scheduled visits.

pause lets the current job finish. stop interrupts it and pauses the queue. resume allows queued and due work to run again. press **Ctrl+C** to stop a CLI run or the server.

to close the game between dashboard visits, enable the idle-close setting or set `close_app_when_idle = true` under `[automation]`. it closes the configured blue archive app once the queue has finished, including after a failed or stopped final job. it doesn't change standalone CLI runs or shut down bluestacks.

## evidence and current limits

runs save an `events.jsonl` log and a ring of the latest 24 screenshots under `data/runs/`, plus retained evidence such as `home.png`, popup before/after pairs, and cafe reward receipts. important actions and scheduling state live in `data/state/`. attempted taps and confirmed results are recorded separately. local config, logs, and full screenshots stay out of git.

the development mac is on bluestacks air **5.21.790.7505**. startup accepted a **332.05 MB** game-data download automatically. after Apple reauthentication, a live restart reached home in **44 seconds**, handling the announcement and new-product popups. initial cafe inspection collected **81 AP and 73,957 credits** and observed one relationship-heart response; full cafe and invitation validation is still in progress.

cafe coverage uses slow pans and measured scene movement, with overlapping views and camera-boundary checks. the full flow is still being calibrated across furniture layouts and has a 15-minute limit. the free-invitation cooldown has also prevented a complete live invitation test. no bulk relationship-collection button has been verified; the gift-panel portraits are a gifting shortcut.

the current active event, lore pursuit, was reached through campaign's upper-left entry. event profiles are local JSON with validated OCR rules and destination checks. recognition helpers are implemented; there isn't an event navigation or farming job yet. event recap is the permanent archive, not the active event.

run offline tests with `.venv/bin/python -m pytest` on mac or `.venv/Scripts/python.exe -m pytest` on windows.

see [the architecture](docs/architecture.md), [cafe behavior](docs/cafe.md), [the home map](docs/home-map.md), [event profiles](docs/event-profiles.md), and [the build order](docs/roadmap.md).
