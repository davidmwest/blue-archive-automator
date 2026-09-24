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

Before Confirm, an atomic per-instance state file records the pending sweep. Completion requires a saved receipt and matching post-sweep ticket and AP balances. An unresolved spend blocks automatic replay, including after daily reset. A reset during an active visit stops further spending so the next run can allocate the new day correctly.

Important actions record the allocation, requested sweeps, verified completions, and skipped areas. Screenshot receipts and full traces stay local. All recognition uses fixed coordinates, image checks, and local OCR; no AI is used during ordinary execution.

## Validation

On September 24, 2026, all 15 Bounty tickets were used: five each on Overpass H, Abandoned Train H (Desert Railroad), and Besieged Classroom H. Each receipt and ticket balance was verified; no AP was spent. A subsequent visit verified zero tickets, no repeated sweeps, and return home. Live testing exposed and fixed the differing area/stage names, split single-digit OCR counters, and the last-ticket flow that returns directly to the list. Classroom's saved receipt and zero-ticket balance were reconciled after that final navigation issue; it was never swept twice.

All 15 Scrimmage tickets were also used, five each on Trinity B, Gehenna B, and Millennium B, at a verified cost of zero AP. Each receipt was saved. The exhausted selector's `0 → –` display was added from its real screen, and Millennium's final receipt/balance was reconciled without another sweep. Both jobs' completed allocations remain 5 / 5 / 5. Live reruns verified zero tickets, no new sweeps, and return home for each task. Nonzero Scrimmage AP costs and uneven weekday splits are covered offline; today's two ticket totals divided evenly. Regression tests cover the weekday allocation, receipt/confirmation disagreements, interrupted spending, locked stages, real sanitized screens, task ordering, and the attendance calendar encountered at daily reset.
