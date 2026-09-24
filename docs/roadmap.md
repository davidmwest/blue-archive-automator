# Build order

## 0. Architecture and repository

Create the private repository, record the design, and identify the local staging instance. Current phase: documentation only.

## 1. Device probe and observation

Implement a CLI that discovers candidate instances, requires an explicit target, and validates its identity before connecting. Never fall back to the first connected device. Include bounded connection retries and a local configuration file.

Read the game package/version, Android version, display density, and capture timings. Require decoded ADB screenshots to be 1280×720 landscape and confirm the initial 320 DPI display profile and expected game layout. Confirm the game client region and language. Save screenshots locally with input disabled. The macOS Blue Archive target and Windows target each need their own verification.

Done when the correct target can be identified and captured repeatedly, disconnects return a clear error, and another running game cannot be selected accidentally. Verify Blue Archive captures while the Azur Lane daemon remains active, using distinct device endpoints and no global ADB resets.

## 2. Perception and replay

Implement fixed-resolution frame validation and the first screen/popup detectors. Use direct pixel coordinates for templates, OCR regions, and input targets. Create reviewed fixtures for the home screen, loading, a disconnected state, an unknown popup, and the first task's screens. Cover wrong-size frames and shifted layouts as well as ambiguous, low-confidence, and successful matches.

Done when recognition runs offline and unknown states produce no proposed live action. Replay demonstrates decision logic; it does not substitute for later live transport tests.

## 3. Navigation and one task

Add action preconditions, explicit pause/resume and stop controls, bounded retries, result verification, and a short screenshot history. Add per-instance/game-account locks and persistent action intent/outcome records before consequential live input. First prove a home-to-menu-to-home route; then complete one selected daily task with a configured resource budget.

Done when a normal run completes, interruption resumes from the observed state, and an uncertain consequential action is reconciled instead of blindly repeated.

## 4. Daily loop and scheduler

Add tasks incrementally: reward collection, cafe, and configured sweeps are candidates, with final ordering chosen for the account. Add task dependencies, server-aware reset scheduling, and richer SQLite run history. Keep tasks serial within an instance while allowing the independent Azur Lane daemon to run concurrently.

Done when the enabled daily loop is repeatable, completed tasks are not needlessly repeated, and failures are diagnosable from local traces.

## 5. Portability, efficiency, and UI

Finish the Windows host adapter and run the same smoke checks on both platforms. Tune CPU/RAM/FPS presets using measurements, then add a local dashboard for task selection, schedules, status, pause/stop, and trace inspection.

The core should remain usable from the CLI. Packaging and automatic emulator lifecycle management follow proven capture/input behavior.
