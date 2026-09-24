# Crafting

use the Quick Craft preset you've already set up in the game. fill the available slots, come back when they're ready, collect the results, and start the next batch if there are keystones left.

## running it

```sh
.venv/bin/python -m ba_automator --config config/local.toml crafting
```

the standalone job runs restart → Crafting → home and saves the slot timers. in the dashboard, **queue crafting** does that same visit. enable **keep the crafting slots busy** to turn the saved timers into automatic collection/refill jobs:

```toml
[crafting]
schedule_enabled = true
```

scheduling is off by default. the setting doesn't change your in-game node priorities or enable an unconfigured preset. Crafting has its own schedule; it's separate from Daily and the Cafe timer.

## each visit

1. Verify Material Synthesis and all three crafting slots, waiting for repeated settled observations.
2. Collect finished slots using an active Claim All control. Verify the reward receipt and the newly vacant slots before logging collection as confirmed.
3. Open Quick Craft if there's space. Require Node 1 to be on and configured, with a recognized keystone-only material list.
4. Select Max. Check that the quantity fits both available slots and keystones, the displayed costs remain consistent, and the confirmation repeats the selected count.
5. Confirm once. Verify new timers in the expected slots before logging the crafts as started.
6. Save every active slot's finish time with a 15-second margin, then return to a clear home screen.

existing credits pay the displayed crafting fee. the job doesn't buy resources, use booster tickets, press Complete Instantly, switch presets, edit nodes, use keystone pieces, or enter Material Fusion. presets with additional material types disable the task with an explanation rather than silently spending those materials.

if no keystones remain, active timers still get collected. when there are no active crafts, it checks again after three hours. unknown inventory is never interpreted as zero; the red zero-keystone counter has its own enlarged OCR read.

## scheduled collection and recovery

the dashboard shows a future collection/refill job for each slot. due slots are combined into one Crafting visit, which checks the actual game before acting. all visits use the ordinary serial queue and instance lock, including manual jobs. the dashboard must stay running; this feature doesn't install a system service.

slot deadlines live in `data/state/crafting-<instance hash>.json`, scoped to the selected ADB endpoint and game package. they survive dashboard restarts even though the ready-to-run FIFO queue is still in memory. overdue work is reconstructed from the saved timers. reads never partially observe a state write because replacement is atomic.

Pause holds dispatch. Stop interrupts the current job and pauses the queue. canceling an already queued automatic Crafting visit defers its recheck by 15 minutes without deleting the slot timers. failures also back off 15 minutes; three consecutive failures pause automatic retries until Resume.

Quick Craft being unavailable, switched off, unconfigured, or requiring unsupported materials disables its schedule persistently. the reason appears in important actions and beside the saved timers. fix the preset in game and manually queue Crafting to recheck it. Resume alone doesn't override a setup disable.

a pending spend or collection is saved before its irreversible tap. an interrupted craft start is reconciled only if the expected occupied slots can be observed. an uncertain collection receipt stops for inspection rather than claiming a reward or repeating the operation. this is recovery with evidence, not an exactly-once guarantee. account switching on one endpoint requires separate state/configuration.

## validation status

the September 24, 2026 live inspection verified the existing Node 1 preset and a maximum batch of three crafts, consuming three keystones and 6,000 credits. the resulting timers were 90 minutes, three hours, and three hours. the empty inventory counter was also captured and recognized. natural completion, the actual collection receipt, and the automated refill path remain pending those timers; offline tests cover their orchestration and spending guards.

sanitized fixtures preserve the relevant UI controls and omit the account resource bar. raw traces and receipts stay in the ignored runtime directories.

Nexon's [Craft guide](https://forum.nexon.com/bluearchive-en/board_view?board=3222&thread=2523180) describes Quick Craft's node setup. the live English 1280×720 interface determines what the runner can recognize and spend.
