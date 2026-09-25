# Spend AP

keep a little AP in reserve. put the rest where you want it.

Open **Spend AP** in the dashboard sidebar, or visit `/ap`. The default floor is **100 AP** and the default strategy is **collect elephs**. Automatic spending starts off; enable it when your plan is ready. Every visit uses the same serial queue and instance lock as the other jobs.

## Pick a plan

- **Collect elephs:** one sweep per Hard stage, then the next stage. The rotation starts with your highest verified three-star clear and ends with your lowest. Its place survives visits and dashboard restarts. Stages with no free attempts left are skipped. Drag stages from “potential” into the rotation and reorder them; add/remove buttons, arrows, and a text editor also work. An empty custom rotation spends nothing.
- **Collect activity reports:** find the highest three-star Base Defense commission, then sweep as many times as the AP floor allows.
- **Collect credits:** the same policy, using Item Retrieval commissions.

**Scan three-star stages** surveys both commission lists and every available Hard mission area without spending AP. Rescan after clearing more stages. A complete scan replaces the catalog; an interrupted scan leaves the previous catalog intact. Editing the rotation only accepts stages in that verified catalog. Commission surveys require overlapping rows, consecutive stable end-of-list observations, and no regression below a previously verified clear. Every spending visit still checks the selected stage, its stars, available attempts, and actual displayed cost.

The floor is a minimum, not an exact target. With 137 AP, a floor of 100, and a 20 AP sweep, the job sweeps once and leaves 117 AP. It never rounds upward, buys AP, resets Hard attempts, or starts an unswept battle.

## Scheduling

When enabled, Spend AP checks hourly and follows Cafe, Mail, and the full Daily plan. A pack visit already ends with Mail, so it also reaches Spend AP. These remain serial tasks; there is no second worker issuing game inputs. Keep the local dashboard running for scheduling to work.

```toml
[ap]
schedule_enabled = false
floor = 100
strategy = "elephs" # elephs, reports, or credits
hard_default_order = true
hard_order = [] # used when hard_default_order is false, e.g. ["13-3", "7-1"]
```

Standalone commands:

```sh
python -m ba_automator --config config/local.toml scan_ap
python -m ba_automator --config config/local.toml spend_ap
```

## Evidence and failures

Each spend records the stage, sweep count, cost, AP before/after, and a saved receipt. Important actions distinguish a requested sweep from a verified result. Raw game captures stay in local run directories and may contain account information.

Before confirming, the runner persists an intent in its per-instance `ap-*.json` state. It then verifies the completion receipt, AP change, and (for Hard missions) remaining attempt count before advancing the rotation. Unknown screens or changed costs stop input. A failed visit pauses automatic spending; after resolving its cause, explicitly queue Spend AP to retry. The CLI equivalent is `spend_ap --retry-ap`.

An unresolved spending intent blocks even an explicit retry. Inspect the actual game, saved receipt, and ledger before reconciling it; restarting the dashboard never silently repeats the sweep. Level-ups that refund AP are currently an inspection case because the result no longer matches the expected AP decrement.

Natural AP regeneration can update the top balance while Mission Info still shows its previous sweep projection. That screen remains readable for result verification, but cannot authorize another sweep until its projection matches the current balance. Item inspection runs before the receipt is dismissed; [Loot Gathered](loot.md) records the exact names, quantities, and icons independently of the final spending check.

A navigation tap can briefly obscure a Hard-area arrow with its blue highlight. If the arrow is missing, the runner takes at most two additional observations of the same area before stopping. A changed page or area still stops input; a recognized arrow must pass the usual freshness and foreground checks. This recovered the September 25 route from area 14 through area 5 to area 1, where the previous run had stopped during the highlight.

Recognition is for the English Global client at 1280×720 and 320 DPI. All runtime decisions use local OCR, image checks, and deterministic policy. [Nexon’s Mission guide](https://forum.nexon.com/bluearchive-en/board_view?board=3222&thread=2720664) documents three-star sweep eligibility and daily Hard attempt limits; execution reads the current screen instead of assuming a fixed AP cost.

## Validation

Offline tests cover floor arithmetic, persistent rotation order, current versus projected Hard attempts, incomplete surveys, ignored scrolls, changed confirmations, missing receipts, and serial task integration. Sanitized live fixtures cover commission lists/details, Hard areas, confirmation, and sweep receipt recognition. The September 24, 2026 live survey found 39 three-star Hard stages (13-3 through 1-1), Base Defense J, and Item Retrieval G. A calibrated credit sweep used 35 AP; the automated report runner selected J and verified a 40 AP sweep. Thirteen Hard sweeps used 260 AP, skipped exhausted higher stages, persisted their cursor across interrupted runs, and stopped at 101 AP with a 100 AP floor. The next saved stage was 4-2. A complete queued rerun, including restart, returned home at 102 AP after natural regeneration, recorded no further spend, and preserved that cursor. Every completed Hard sweep verified a receipt and the current daily-attempt decrement; the completed visit returned home.

Calibration also found an ignored-scroll bug that selected report stage E for one 25 AP sweep. Consecutive boundary observations and a guard against downgrading a known clear now cover that failure. Real-screen regressions cover small/missing stage indices, split title boxes, and the distinction between projected and current Hard attempts.

Commission Max, Min, plus, and minus controls were also checked live, including a two-sweep projection, without spending additional AP. Larger commission-batch receipts and AP-refunding level-ups still need live coverage; larger-count budgeting and confirmation checks have offline coverage.
