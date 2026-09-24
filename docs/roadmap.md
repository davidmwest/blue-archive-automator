# Build order

## 1. Restart and diagnostics — live verified

The private repository contains the CLI, explicit-target ADB transport, local TOML configuration, per-instance lock, OCR/template recognition, and bounded startup task. `probe`, `capture`, and `inspect` support diagnosis without game input. Popup dismissal records preserve before/after evidence.

The development mac was updated to BlueStacks Air **5.21.790.7505**. A live startup automatically accepted a **332.05 MB** game-data download. Apple reauthentication was completed, and a subsequent restart reached a clear home screen in **44 seconds** with three actions, including the announcement and new-product overlays.

Continue adding reviewed fixtures for new startup screens and keep download, interruption, unknown-screen, and timeout cases bounded. Full raw screenshots stay local.

## 2. Dashboard and cafe — implemented, live validation in progress

The loopback dashboard provides a serial job queue, settings, stop/pause controls, a visual home map, run evidence, and persistent important-action history. Its optional cafe schedule waits three hours and 15 seconds after success, retries failures after 15 minutes, and pauses after three consecutive failures. The queue is temporary; schedule and action records survive server restarts.

An optional setting closes Blue Archive after the last dashboard job exits and the queue is empty. It leaves the emulator and server running; the next task starts through restart. It defaults to off and does not affect standalone CLI runs.

`daily` and `cafe` run restart first, then collect cafe earnings, check students across unlocked floors, and return home. Optional exact-name invitations use the free route. The initial live inspection collected **81 AP and 73,957 credits** and observed relationship-heart feedback from one interaction.

Complete cafe validation with repeatable full runs, floor switching, empty earnings, occluded/disappearing attention markers, cooldowns, invitation selection and confirmation, and interruption. Report scan completion separately from confirmed student interactions. Keep invitations and scheduling off by default until explicitly enabled.

Camera calibration is an immediate remaining step. Slower pans exposed additional markers. The runner now measures scene displacement, scans overlapping views, and requires observed stationary drags to establish camera bounds, within a 15-minute visit budget. It has no zoom dependency; the unverified raw-pinch experiment was removed. Validate camera movement and overlap instead of assuming each requested pan exposes a new area. A free-invitation cooldown has prevented the complete live invitation flow; exact-name row selection and confirmation remain to be checked when available. The Gift panel was inspected, and no bulk relationship-collection button was verified.

## 3. Event navigation

Live inspection verified Home → Campaign → the upper-left Lore Pursuit entry → spoiler notice → play guide → the event page with Quest/Challenge controls. Event Recap remains a separate permanent archive. The entry position alone does not identify the rotating card.

Local version-1 event JSON and strict read-only recognition helpers are implemented. Next add a bounded navigation job using reviewed event identity, fresh frames, optional guarded notices, and destination checks. There is no event job or farming task yet. Research each event ahead of time; no runtime AI is needed to interpret the profile.

Before farming, define playable-period checks, stage selection, resource limits, and observable sweep completion. Main Story, Mission, Event Recap, event shops, and timed event stages need separate destinations.

## 4. Portability and coexistence

Smoke-test installation, screenshot capture, input, restart, cafe, and interruption on Windows with BlueStacks 5 at the same 1280×720 and 320 DPI profile.

Run Blue Archive while the Azur Lane daemon is active on a different endpoint with a compatible shared ADB server. Confirm startup and recovery leave the other game connected. Measure OCR/capture latency and combined CPU, GPU, and memory use before choosing resource presets.

## 5. Additional daily tasks and durable execution

Add one routine at a time after restart, with recognized entry/exit states, bounded actions, and replay coverage. Reward collection and configured mission sweeps are candidates. Introduce spending/resource budgets only as needed for an implemented task.

Add daily-reset-aware scheduling and task dependencies when the daily plan grows. The current scheduler is the cafe cooldown interval, not a complete server-day planner. Add SQLite history, resumable checkpoints, or account-level coordination when their use cases exist.

Platform-specific emulator discovery/start/stop, packaging, and optional system-service installation follow reliable task execution. The current server must be launched and kept running manually.
