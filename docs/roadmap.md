# Build order

The [high-level design](design.md) defines the intended direction and task contracts. This page records implementation progress and live evidence.

## Portfolio foundation

The repository includes a high-level design, an engineering case study, setup and contributor guides, and MIT licensing for original code with separate notices for game-derived images. A read-only demo on port 8766 runs from the standard library with fictional records and original illustrations. It shares the real dashboard frontend but has no device controls or access to local account data. CI checks the source tests and built distributions, including an installed-wheel demo smoke test.

## 1. Restart and diagnostics — live verified

The private repository contains the CLI, explicit-target ADB transport, local TOML configuration, per-instance lock, OCR/template recognition, and bounded startup task. `probe`, `capture`, and `inspect` support diagnosis without game input. Popup dismissal records preserve before/after evidence.

The development mac was updated to BlueStacks Air **5.21.790.7505**. A live startup automatically accepted a **332.05 MB** game-data download. Apple reauthentication was completed, and a subsequent restart reached a clear home screen in **44 seconds** with three actions, including the announcement and new-product overlays.

On September 24, a force restart recovered a frozen publisher splash and verified home in 43 seconds. Startup now retries one force-stop/relaunch when its startup timer expires, preserving the instance lock, download deadline, and total tap limit. A live fault-injection check expired only the first attempt’s timer early; the automatic retry dismissed an announcement and verified home in 72 seconds total. Offline tests cover exhausted retries, interruption, preserved download deadlines, fresh-frame input, and preventing downstream spend replay.

Continue adding reviewed fixtures for new startup screens and keep download, interruption, unknown-screen, and timeout cases bounded. Full raw screenshots stay local.

## 2. Dashboard and cafe — full visit live verified; invitations pending

The loopback dashboard provides a serial job queue, settings, stop/pause controls, a visual home map, run evidence, persistent important-action history, and full daily text logs. Its optional cafe schedule waits three hours and 15 seconds after success, retries failures after 15 minutes, and pauses after three consecutive failures. The queue is temporary; schedule, action, loot, and daily-log records survive server restarts.

An optional setting closes Blue Archive after the last dashboard job exits and the queue is empty. It leaves the emulator and server running; the next task starts through restart. It defaults to off and does not affect standalone CLI runs.

`cafe` runs restart → Club → mail → Cafe, followed by Spend AP when enabled. The Cafe routine collects earnings, checks students across both unlocked floors, and returns home; Daily uses the same routine before its ticket and lesson work. A live full run completed in **425.5 seconds**, recognized empty earnings, verified measured scans and camera boundaries on both floors, returned home, and closed the game. Earlier receipts verified **81 AP and 73,957 credits**, then **16 AP and 14,791 credits**; relationship hearts and rank-up screens were also verified. The full passing run recorded zero new relationship increases.

Continue validation with repeat visits after the relationship cooldown, occluded/disappearing attention markers across more layouts, invitation selection and confirmation, and interruption. Report scan completion separately from confirmed student interactions. Keep invitations and scheduling off by default until explicitly enabled.

The runner measures scene displacement, scans overlapping views, and requires two observed stationary drags to establish camera bounds, within a 15-minute visit budget. Unmeasurable movement triggers an intermediate student scan and resets distance/boundary evidence before another short drag. Camera movement also passed a separate check in another player's furniture layout. The runner has no zoom dependency. A free-invitation cooldown has prevented the complete live invitation flow; exact-name row selection and confirmation remain to be checked when available. The Gift panel was inspected, and no bulk relationship-collection button was verified.

## 3. Lessons — relationship routine live verified; school rank-up pending

The default relationship strategy compares rooms across all unlocked locations and maximizes owned-student opportunities. The alternative school-rank strategy selects the lowest rank and XP progress, then the fullest room, breaking ties with owned relationship ranks. The planner operates on observed data without device access or runtime AI.

The runner surveys every unlocked school and reconciles the observed ranks with Total Area Rank before each ticket. It verifies the selected room, confirms one existing ticket, then checks the result and ticket decrement. Configurable ticket limits, eligible location names, and daily inclusion are available in TOML and the dashboard. Missing comparison evidence or an unknown configured location stops spending.

`lessons` runs restart → Lessons. The full `daily` plan now includes Club, free packs, mail, Cafe, Bounties, Scrimmages, Tactical Challenge rewards, Lessons, and Tasks rewards, with configurable daily inclusion for ticket/lesson jobs and opt-in paid packs and AP spending. All successful plans finish with a home-notification scan. The three-hour Cafe timer checks Club and mail but does not spend lesson tickets. A completed Cafe visit still updates its schedule if later Lessons work fails or is interrupted. No daily-reset Lessons timer or lesson-ticket purchase flow has been added.

Seven authorized tickets were used across calibration and guarded runs, with seven matching receipts and ticket decrements, in rooms containing two owned students each. The final uninterrupted job used the remaining two tickets, returned to a verified home screen, and closed the game in 475.3 seconds including restart. Every ticket compared 94 room cards across 12 unlocked locations with Total Area Rank 80. The [Lessons validation section](lessons.md#validation-status) records the limits and evidence.

Offline coverage includes both policies, incomplete evidence, config validation, job sequencing, stop/pause behavior, and preserving Cafe scheduling after later task failure. The school-rank policy is tested with changing rank/XP observations; its actual in-game rank-up interruption still needs a captured live case. Recognition of an explicit rank-up title with an active Confirm button has synthetic coverage, and unsupported variants stop.

## 4. Event navigation

Live inspection verified Home → Campaign → the upper-left Lore Pursuit entry → spoiler notice → play guide → the event page with Quest/Challenge controls. Event Recap remains a separate permanent archive. The entry position alone does not identify the rotating card.

Local version-1 event JSON and strict read-only recognition helpers are implemented. Next add a bounded navigation job using reviewed event identity, fresh frames, optional guarded notices, and destination checks. There is no event job or farming task yet. Research each event ahead of time; no runtime AI is needed to interpret the profile.

Before farming, define playable-period checks, stage selection, resource limits, and observable sweep completion. Main Story, Mission, Event Recap, event shops, and timed event stages need separate destinations.

## 5. Portability and coexistence

Offline tests run on macOS, Windows, and Ubuntu in [GitHub Actions](https://github.com/davidmwest/blue-archive-automator/actions/workflows/tests.yml). They verify implementation behavior without an emulator; they do not establish live platform support.

Smoke-test installation, screenshot capture, input, restart, cafe, lessons, and interruption on Windows with BlueStacks 5 at the same 1280×720 and 320 DPI profile.

Run Blue Archive while the Azur Lane daemon is active on a different endpoint with a compatible shared ADB server. Confirm startup and recovery leave the other game connected. Measure OCR/capture latency and combined CPU, GPU, and memory use before choosing resource presets.

## 6. Club and additional daily tasks

Club is active after restart in Daily and Cafe plans, and is available independently. It requires Social and Club notification badges, verifies entry, and persists a per-instance check for the game day beginning at 19:00 UTC. The September 24 live run verified the attendance notice, its 10 AP mailbox receipt, and return home. Same-day suppression and interrupted visits have offline coverage. See [Club attendance](club.md).

Free packs, mail, and completed Tasks have collection jobs. Successful runs scan home red dots and request the relevant serial follow-ups. Paid-pack renewal is independently configurable and off by default; uncertain charges create a durable hold. The inspected AP-pack purchase and mail receipt were verified live, while automated charge decisions and recovery were tested offline. See [red-dot dispatch](red-dots.md), [Tasks rewards](task-rewards.md), and [packs and mail](packs-and-mail.md).

Bounties and Scrimmages split available tickets across three areas, rotate extras by game-day weekday, and sweep the highest observed three-star stages. Both jobs completed live 15-ticket allocations of 5 / 5 / 5; Scrimmages cost zero AP on that account. Uneven allocations and nonzero Scrimmage AP costs remain offline-tested cases. [Ticket sweeps](tickets.md) records the receipt and recovery checks.

Tactical Challenge collects available rewards and verifies that battle tickets are unchanged. Time Reward collection has passed live; an available Daily Reward still needs live validation. Fighting remains a no-input stub outside dispatch. See [Tactical Challenge rewards](tactical-challenge.md).

## 7. Durable execution

Keep extending one routine at a time, with recognized entry/exit states, bounded actions, and replay coverage. Reward collection and configured mission sweeps already follow this contract. Crafting, AP, ticket sweeps, Club, and paid packs have durable task-specific state; unresolved resource actions must be reconciled before further spending.

Add daily-reset-aware scheduling and task dependencies when the daily plan grows. The current scheduler handles Cafe cooldowns, craft deadlines, optional pack checks, and hourly AP checks; it is not a complete server-day planner. Add SQLite history, general resumable execution, or account-level coordination when their use cases exist.

Platform-specific emulator discovery/start/stop, packaging, and optional system-service installation follow reliable task execution. The current server must be launched and kept running manually.

## Crafting collection timers

Quick Craft has a guarded affordable-batch runner and per-instance durable collection deadlines. The live three-keystone batch started with 90-minute/three-hour/three-hour timers; all three natural completions passed, including a combined visit for the two later slots, verified rewards, empty slots, zero-keystone inventory, and return home. An evening check exposed the game's unaffordable Max selection; incremental, cost-checked quantity selection replaced it. The subsequent two-keystone refill spent 4,000 credits, verified two three-hour timers, and returned home without changing the preset. Collection immediately followed by refill in the same visit, resetting an initial quantity with Min, and changed-cost cases still have offline coverage only. The dashboard combines due slots through its serial queue and persists setup-disable reasons. See [Crafting](crafting.md).

## AP spending

A dedicated planning page provides a minimum AP floor, report/credit commission policies, and an editable Hard-stage round robin. The runner scans three-star clears, reads live costs, verifies every spending confirmation and receipt, and retains a durable rotation cursor and unresolved spend intent. Optional hourly visits and follow-ups after Cafe/Mail use the existing serial queue. See [Spend AP](spend-ap.md) for setup and validation status.

## Loot and daily logs

Loot Gathered now reads reward-card tooltips, stores exact names, quantities, and game icons, groups matching items, and orders categories by importance. Live checks covered ordinary sweep totals, expanded Full List receipts, and a Tactical Challenge reward card; three authorized sweeps used 60 AP while the configured 100 AP floor stayed unchanged. Long scrolling lists and other task integrations retain offline coverage. Older screenshots may lack item names or proof that every card was captured, so review notices distinguish unread details, an unverified full list, and missing details. Clearing totals preserves the evidence. See [Loot Gathered](loot.md).

The Important Actions header links to a full plain-text log saved as `YYYY-MM-DD.log` using the computer's local calendar. Queue events, runtime decisions, resource outcomes, and important actions share that durable view; existing journals can be imported without duplicating events, with estimated historical timestamps marked. Local logs and account evidence remain outside the portfolio repository. See [daily logs](daily-logs.md).
