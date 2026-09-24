# contributing

start with a small change and a concrete failure or behavior to improve. the [high-level design](docs/design.md) sets the direction, the [architecture](docs/architecture.md) describes today's implementation, and the [engineering notes](docs/engineering.md) explain some decisions behind it.

## run the tests without an emulator

CI uses Python 3.11 on macOS, Windows, and Ubuntu. installing dependencies needs a network connection; the test suite uses fake devices, synthetic images, and reviewed screenshot fixtures. it does not require ADB, BlueStacks, or a game account.

on macOS or Linux:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

on Windows, in PowerShell:

```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m pytest -q
```

the commands use the virtual environment directly, so shell activation is optional. for a focused camera change, for example:

```sh
.venv/bin/python -m pytest tests/test_cafe_camera.py tests/test_cafe_scan.py -q
```

## make the behavior reviewable

describe what triggered the problem, what happened before, and what should happen after. add a regression test when it protects a meaningful behavior: an expired frame must not send input, an ambiguous dialog must not become a purchase, and unknown camera movement must not prove a boundary. avoid tests that just restate an implementation detail.

device, process, and clock dependencies are injectable. use those seams for offline tests; a unit test should never reach a real emulator. keep input bounded and tied to fresh evidence. record attempted actions separately from verified outcomes.

run the relevant tests while editing and the full suite before submitting a code change. report the commands and results, plus anything you could not verify. CI passing on Windows verifies the Python implementation there; it does not establish that a particular BlueStacks setup works. live tests need a deliberately selected staging instance using the fixed English display profile in the [setup guide](docs/getting-started.md).

## share useful evidence, not account data

for recognition bugs, include the task, expected state, observed state, display dimensions, and a minimal reproduction. prepare a sanitized fixture that preserves the relevant pixels and coordinates, following [the fixture notes](tests/fixtures/README.md). prefer generated images for camera geometry and other cases that do not need game artwork.

keep local TOML, raw run directories, full account screenshots, account identifiers, login screens, credentials, and tokens out of commits and issue attachments. review the actual image as well as its filename before sharing it. `.gitignore` is a convenience, not a privacy review. a screenshot crop can still contain private information.

new game-image fixtures need a clear source and a reason the test needs them. the project's source-code license does not grant rights to game artwork; see [third-party notices](THIRD_PARTY_NOTICES.md). preserve dependency and upstream license notices when adding third-party material, and document any reused code or assets explicitly.

## keep the scope clear

event rules belong in reviewed [event profiles](docs/event-profiles.md). runtime behavior should remain local and reproducible, with no required LLM service. a new job should use the existing queue and instance lock, leave evidence when it stops, and state its live validation limits. controllers targeting the same instance must share `lock_dir`. changes to the intended execution model belong in the high-level design before implementation. a short pull request explaining the change and its evidence is enough.
