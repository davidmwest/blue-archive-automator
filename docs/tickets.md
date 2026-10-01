# Bounties and Scrimmages

split the tickets three ways, then sweep the highest three-star stage in each area. no ticket purchases, no manual battles.

Both jobs have their own queue button and CLI command (`bounties`, `scrimmages`). They start with Restart and return home afterward. Daily includes both, after Cafe and before Lessons / Spend AP. Each has an **include in daily** switch; this does not create a separate background schedule.

## Dividing tickets

Use the available ticket count on the first visit of the game day. Each area gets `tickets // 3`. Extras start at `weekday % 3`, with Monday = 0; a second extra goes to the next area, wrapping around.

| Index | Bounties | Scrimmages |
| --- | --- | --- |
| 0 | Overpass | Trinity |
| 1 | Desert Railroad | Gehenna |
| 2 | Classroom | Millennium |

For example, Thursday has weekday 3. Eight tickets become **3 / 3 / 2**; Friday's eight tickets become **2 / 3 / 3**. The weekday comes from the Global game day, which changes at **19:00 UTC**, rather than the computer's midnight. The weekly rollover deliberately follows weekday modulo 3.

The initial allocation and completed counts survive restarts. Retrying the same day finishes the original allocation. Tickets added after that snapshot stay for the next game day. An unexpected decrease stops the job rather than silently changing the split. A school with no three-star clear keeps its share; that share is not moved elsewhere.

## Sweeping and evidence

The job surveys the complete stage list with overlapping scrolls, checks stars and locked rows, selects the highest verified clear, and rechecks its detail screen. It reads the ticket and AP projections, sets the exact quantity, and checks the confirmation's resource cost before spending. Scrimmages respects the shared AP floor; a zero-AP sweep remains allowed even when AP is below that floor.

If the quantity selector jumps past its target, the job resets it to Min and observes each adjustment again. Recovery is bounded; only the exact allocated count can proceed to the separate spending confirmation.

Before Confirm, an atomic per-instance state file records the pending sweep. Completion requires a saved receipt and matching post-sweep ticket and AP balances. An unresolved spend blocks automatic replay, including after daily reset. A reset during an active visit stops further spending so the next run can allocate the new day correctly.

A confirmation that was never accepted can be recovered on the same game day. Two fresh task-menu observations, at least two seconds apart, must show the unchanged pre-confirmation ticket balance and agree with the saved allocation. Only then is the pending intent cleared; completed counts stay unchanged, and any new sweep must repeat the normal stage and cost checks. A decreased balance, stale frame, different task, or daily reset leaves the spend unresolved. Both observations and an important action record explain the recovery; no reward is inferred from unchanged tickets.

Important actions record the allocation, requested sweeps, verified completions, and skipped areas. Screenshot receipts and full traces stay local. All recognition uses fixed coordinates, image checks, and local OCR; no AI is used during ordinary execution.

## Validation

On September 24, 2026, all 15 Bounty tickets were used: five each on Overpass H, Abandoned Train H (Desert Railroad), and Besieged Classroom H. Each receipt and ticket balance was verified; no AP was spent. A subsequent visit verified zero tickets, no repeated sweeps, and return home. Live testing exposed and fixed the differing area/stage names, split single-digit OCR counters, and the last-ticket flow that returns directly to the list. Classroom's saved receipt and zero-ticket balance were reconciled after that final navigation issue; it was never swept twice.

All 15 Scrimmage tickets were also used, five each on Trinity B, Gehenna B, and Millennium B, at a verified cost of zero AP. Each receipt was saved. The exhausted selector's `0 → –` display was added from its real screen, and Millennium's final receipt/balance was reconciled without another sweep. Both jobs' completed allocations remain 5 / 5 / 5. Live reruns verified zero tickets, no new sweeps, and return home for each task. Nonzero Scrimmage AP costs and uneven weekday splits are covered offline; today's two ticket totals divided evenly. Regression tests cover the weekday allocation, receipt/confirmation disagreements, interrupted spending, locked stages, real sanitized screens, task ordering, and the attendance calendar encountered at daily reset.

September 26 saved failures added regressions for a Bounty quantity jump from four to seven and separately recognized Scrimmage AP/ticket projections. Selector recovery and rejection of ambiguous or inconsistent projections are covered offline. Live Bounty retries finished the remaining Desert Railroad and Classroom allocations at H. Full-frame OCR omitted the dash in the final `0 → –` selector; the fallback requires agreeing enlarged reads of that projection and exposes no Sweep target. The saved Classroom receipt and live zero balance were reconciled without repeating it. A subsequent visit verified all 15 allocated tickets used, zero remaining, and return home.

The September 26 Scrimmage visit also completed five sweeps in each area at B. Whole-frame OCR omitted the entire exhausted ticket label after Millennium, so a separate crop fallback was verified against the saved screen and the live selector. The saved receipt, zero-ticket balance, and unchanged AP reconciled that final sweep without replaying it. A fresh visit then verified all 15 allocated tickets used, zero remaining, and return home. The fixture tests require two complete, high-confidence reads and reject inconsistent resource or stage evidence; reward items without a readable quantity remain explicitly incomplete in the loot log.

After the September 26 daily reset, Trinity B again completed five sweeps with a verified 15-to-10 ticket change. The following Gehenna confirmation remained unchanged throughout the failed visit; it produced no receipt to recover. A live recovery visit then verified the unchanged ten-ticket balance twice before releasing that intent. It completed Gehenna B and Millennium B, five sweeps each, verified both receipts and the zero-ticket balance, and returned home. The completed allocation is 5 / 5 / 5 with no pending spend and no AP cost. The earlier screenshot alone was not used as proof that the tickets remained unspent.

September 27 testing at 1440p accounted for all **15 Bounty and 15 Scrimmage tickets**, five per area at H and B respectively, with zero AP cost. These allocations finished across recovery runs; neither represents an uninterrupted full-task pass. Both final sweeps produced five-run receipts and exhausted selectors, but recognition of the final zero-ticket counter failed. The saved receipts, post-sweep balances, and original requests were preserved with file hashes and reconciled under the instance lock without repeating either sweep. Both allocations are 5 / 5 / 5 with no pending spend. The exhausted Scrimmage crop also excludes the cost bubble's lower border, which interfered with reading the final dash; its confidence thresholds remain unchanged.

A separate Bounty visit at **16:12 PDT** verified 15 / 15 allocated tickets used, zero remaining, and return home without another sweep. Further visits at **16:47 PDT** for Bounties and **16:48 PDT** for Scrimmages independently verified the completed allocations, zero tickets, and return home. That Daily finished successfully at **17:10 PDT**, with its full scope recorded in the [recovery notes](engineering.md#recovering-the-first-native-resolution-daily). Some Bounty reward items still require identification; verified ticket spending does not make an incomplete loot receipt complete. Nonzero Scrimmage AP costs and uneven ticket splits remain covered offline.

On October 1, whole-frame OCR omitted the arrow from Scrimmage's `427 → 427` AP projection after a completed Trinity B sweep. A narrow native-resolution fallback now requires two agreeing, high-confidence readings and agreement with both surviving digits. The independent ticket, AP-balance, stage, and cost checks remain required. A sanitized regression fixture covers the actual screen and rejects contradictory or incomplete readings. Trinity's saved five-sweep receipt, post-sweep balances, and two fresh menu observations reconciled the pending intent without another sweep or duplicate loot. The live recovery then completed Gehenna B and Millennium B, five sweeps each, and returned home with all 15 allocated tickets used, zero remaining, and no AP cost. This verifies recovery across runs, not an uninterrupted 15-ticket visit.

Windows and Linux regression runs exposed a crop edge that macOS tolerated: a sliver of the AP lightning icon was read as a leading `B`. The crop now excludes the entire icon and lower border. Exact arrow syntax, both matching reads, and the existing confidence threshold remain required.
