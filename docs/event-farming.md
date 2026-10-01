# Event farming

**Status:** quest clearing, seasonal navigation, and optional event-first AP routing are implemented. Quest clears and event sweeps have live evidence at 1440p. Treasure Hunt now has an independent opt-in runner with journaled cell spending and reward receipts. Event shops remain unimplemented.
**Researched:** September 29, 2026 · global English

farm what we actually want, collect it, then get back to the daily stuff. a new banner shouldn't require a new automator.

This extends the implemented [event profile recognition](event-profiles.md). The runtime uses local JSON, image recognition, OCR, and ordinary code. Research and profile preparation happen before release; no AI is needed while playing.

## The event that just launched

The current event is **A Flower Blooms Among the Hundred: Fair and Square Aquatic Showdown (rerun)**. Play ends **October 13, 2026 at 01:59 UTC**; shop, task, and reward exchange access ends **October 20 at 01:59 UTC**. Bonuses are retained per stage and currency for sweeps. Treasure Hunt spends **200 Wooden Yukari Dolls per cell**, awards Wooden Renge Dolls, and allows irreversible board refresh after uncovering a treasure. These details come from the [current event thread](https://www.reddit.com/r/BlueArchive/comments/1wt161e/), which links [Nexon's patch notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=3549872&allBoard=1). The official page and linked fan wiki could not be fetched during this research; release still requires checking the client.

The [rerun farming guide](https://www.reddit.com/r/BlueArchive/comments/1wt161e/comment/pcqb1ga/) recommends stages 10–12 for their artifact mix, despite stage 9's slightly better currency efficiency. It recommends three treasure rounds for pyroxene/eligma, or six for broader rewards; round 7 onward has poor value. This is advice, not a universal optimum. Actual bonuses and chosen rewards determine our plan.

### Proposed starting preset

- Use already three-starred event quests. Prefer stages 10–12 when available; consider other verified stages when their observed yields better serve the selected goals.
- Prioritize available pyroxene and eligma exchanges. Offer Izuna elephs, activity reports, artifacts, and furniture as individually editable targets. Show limited furniture explicitly; do not silently buy duplicates.
- Default Treasure Hunt goal: the valuable rewards through round 3, stopping once the selected exchange targets are funded. Offer a six-round preset and manual targets. Never enter repeating rounds by default.
- Preserve the existing **100 AP floor** and the user's automatic-spending switch. No AP purchases, attempt resets, recruitment, or other resource purchases are implied.
- Event Stories run first when event AP priority is enabled; each episode preserves the shared AP floor. Initial quest clears can be explicitly requested from the dashboard and use the game's default Auto Formation each time. Specialized bonus-team optimization remains future work.

The reviewed asset uses season ID `aquatic-showdown-global-2026-rerun`. Original-run shop and reward tables must not be copied without comparison. Navigation, early quest costs and results, and the treasure entrance have live evidence. Later stage costs and shop stock still require observation. Treasure Hunt geometry and the 200-currency reveal confirmation were verified in the client.

## One runner, small event profiles

| Component | Responsibility |
| --- | --- |
| Event registry | Resolve server, season, profile revision, availability, and supported mechanics |
| Event inspection task | Reach the identified event, read progress/currencies/unlocks, collect enabled free rewards, and propose work |
| AP planner | Choose between event goals and the existing Hard/commission plan using one shared budget |
| Event sweep executor | Verify stage/stars/cost, sweep a bounded batch, reconcile AP and receipts |
| Mechanic adapters | Execute reviewed shop, exchange, lottery, or board rules with typed observations |
| Persistent ledger | Store progress, pending transactions, budgets, receipts, and continuation state |

Profiles supply **data**: identities, screen anchors, stage tables, item IDs, dates, objectives, and parameters for supported adapters. They cannot supply Python, shell commands, arbitrary scripts, or unrestricted click sequences. A genuinely new mechanic requires a new tested adapter. An unfamiliar event gets a visible “needs an event profile” status; it is never treated as whichever event occupied that button last week.

Suggested modules are `event_registry`, `event_planner`, `event_runner`, and `event_mechanics/*`, alongside the existing `events.py` recognition helpers. These names are proposals, not existing APIs.

## Configuration contract

Keep the existing version-1 recognition loader backward compatible. Introduce a separately validated **version-2 farming schema** when its runner exists. Do not add unsupported farming fields to today's version-1 files.

| Profile data | Required contract |
| --- | --- |
| Identity | Event ID, server, run/season, schema version, profile revision, minimum compatible runner |
| Availability | Separate playable and reward cutoffs; unknown maintenance completion is not a fabricated timestamp |
| Navigation | Recognized entrance and destination, bounded carousel search, guarded notices |
| Stages | Stable IDs, recognizable labels, expected AP cost, reward IDs, bonus rules, unlock prerequisites |
| Shops | Currency and item IDs, finite/repeatable stock, exchange rates, recognition anchors |
| Mechanics | Allowlisted adapter/version plus validated parameters and supported phases |
| Evidence | Research URLs/date, sanitized fixture references, and which behavior has passed live verification |

Account preferences belong outside the shared profile: enabled flags, AP budget, targets, reserves, preferred stages, and whether to spend event currencies. State is keyed by instance/account boundary, server, and season; a rerun never inherits an old board or completion marker.

Conceptual settings, **not accepted by the current config parser**:

```json
{
  "events": {
    "enabled": true,
    "ap_priority": "event_goals_first",
    "daily_ap_limit": 600,
    "season_ap_limit": null,
    "claim_free_rewards": true,
    "spend_event_currency": true,
    "active_preset": "aquatic-showdown-three-rounds"
  }
}
```

The 600 AP value illustrates a user-selected budget, not a recommendation or promise that the event can be completed. Defaults should show the proposed budget and targets before enabling spending. A profile upgrade is validated at idle, with a pinned revision per visit. Incompatible state migrations require reconciliation, never resetting spending history.

## AP planning without two competing spenders

`spend_ap` remains the sole owner of AP allocation. Event inspection can request it, but cannot launch an independent AP-spending loop. Daily, check-ins, AP collection follow-ups, and “do everything” all reach the same deduplicated planner.

Selectable policies:

1. **Event goals first:** fund event goals within their budget, then resume the configured Hard/commission plan.
2. **Hard rotation first:** finish the configured daily free Hard attempts, then event goals, then the usual fallback.
3. **Fixed event allocation:** reserve a configured amount of today's available AP for the event; use the rest normally. Never reserve more than is spendable above the floor.

For each decision, read fresh AP and compute available AP after the shared floor, remaining daily/season limits, and any reserved transaction. Read remaining shop stock and owned currency; subtract currency already held from goal costs. Treasure goals contribute an estimate with a displayed range, not a guaranteed price.

Compare verified stage yield vectors using the account's saved bonuses. For fixed targets, use a bounded integer allocation minimizing AP subject to currency deficits; account for exact game rounding. If the budget cannot satisfy all targets, satisfy priority tiers in order and report the unmet targets. Break equivalent plans by configured artifact preference, then fewer navigation changes, then stable stage ID. Avoid a single “best stage” score that overvalues an already-funded currency.

Use small batches and replan after verified receipts. A cost or yield mismatch invalidates the affected catalog and triggers inspection before more spending. Event bonuses, AP regeneration, and recognized level-up refills must use the existing AP reconciliation rules. Seasonal goals and budgets survive restart; daily budgets reset on the configured **game day**, not the machine's midnight.

When goals are complete or an event is unavailable, normal AP work continues. Unsupported setup only skips event spending with an explanation. An unresolved AP transaction still blocks all AP spending: falling back must not bypass the existing resource hold.

## Navigation and scheduling

Use Campaign or Home only after recognizing the event's identity. Both banner positions can rotate; coordinates alone are insufficient. Verify the destination title and expected controls after entry. Event Recap is not an active-event shortcut. See [navigation research](event-navigation-research.md).

Inspect enabled events daily regardless of badge color, and after relevant check-ins when due. Reward-only visits remain eligible until their separate cutoff. Today, `match_entries` excludes reward-only profiles; implement an explicit reward-navigation mode rather than weakening the farming date guard.

Multiple profiles can coexist: Lore Pursuit's reward window can overlap this new event. Choose by identity and purpose, not “latest banner wins.” Red dots may request inspection but never establish spend eligibility.

Each visit handles one bounded sweep or exchange batch, returns to a verified safe screen, and yields to due Cafe/Crafting work. Persist a continuation for remaining work. Queue pause/stop, instance locking, backoff, and idle-close apply unchanged. A completed inspection is not the same as completed farming; the dashboard must distinguish “waiting for AP,” “needs setup,” “goals complete,” and “needs review.”

## Treasure Hunt adapter

This mechanic needs its own implementation, not a list of blind taps. [Joe's solver](https://ba.joexyz.online/inventory-management) demonstrates placement-based reasoning and warns that its probability estimates are not guarantees. Our runtime must work locally and must not call that website to decide moves.

1. Recognize event, round, board dimensions, revealed cells, currency, and available treasures. Load the matching reviewed round definition.
2. Enumerate placements consistent with observed cells, permitted orientations, and non-overlap. Use bounded deterministic enumeration; if the budget is exceeded, stop without a move rather than score a biased partial enumeration.
3. Choose the unopened cell intersecting the most distinct feasible placements of still-desired treasures, as specified below. One cell per transaction; then reread the board.
4. Maintain a separate board-spend limit and stop if geometry, cell evidence, or remaining currency is uncertain. Partial board observations never justify a reset.
5. Refresh only when the client allows it **and** all selected targets for this round are verified complete or explicitly skipped by the selected preset. Show the skipped rewards in the action log. Persist reset intent and verify the new round before another cell.
6. Stop at the selected round/goal limit. A six-round preset must not accidentally advance into repeating rounds.

For the three-round preset, encode the reviewed guide's chosen treasures rather than “refresh after the first prize.” Confirm the round reward table and target funding in the client before enabling that preset. If those rules cannot be established, stage farming and shops can ship with Treasure Hunt marked manual; the runner must not keep farming unlimited minigame currency.

### Implemented greedy policy

`ba_automator/treasure_policy.py` contains both the original bounded exact planner and the runtime's faster local greedy planner. The live adapter reads a 5 × 9 board and remaining counts from each supply card, allowing rotation. `treasure_rounds.py` defines the round-specific layouts from the [rerun reward tables](https://bluearchive.wiki/wiki/A_Flower_Blooms_Among_The_Hundred_%EF%BD%9E_Honorable_Sea_Showdown_%EF%BD%9E/Rerun):

| Rounds | Prize rectangles and initial counts |
| --- | --- |
| 1, 4 | 2 × 3: 2; 1 × 3: 5; 1 × 2: 2 |
| 2, 5 | 2 × 4: 1; 1 × 4: 2; 1 × 3: 5 |
| 3, 6 | 3 × 3: 1; 2 × 2: 4; 1 × 2: 3 |
| 7 onward | 2 × 4: 2; 1 × 3: 3; 1 × 2: 6 |

These definitions validate observations; they do not extend the configured three-round spending goal.

For every remaining shape, enumerate distinct rectangular placements that avoid confirmed empty cells and completed prizes. Score each closed tile lexicographically:

1. Placements whose only unopened tile is this one.
2. Placements through this tile that include a known, unfinished hit.
3. All feasible placements through this tile.
4. Row/column order for reproducible ties.

This favors finishing exposed prizes, then investigating the largest number of plausible hiding places. It is a local heuristic, not an exhaustive non-overlapping arrangement search, calibrated probability, or global resource optimum. Anonymous hits do not identify a prize until its inventory count decreases and a unique completed rectangle is established. Contradictory boards fail without another purchase.

The `[ap].event_treasure_enabled` switch is independent of `[ap].event_priority`, off by default, and appears beside event farming on the Spend AP page. When enabled, daily work visits Treasure Hunt and AP jobs follow farming with Treasure Hunt, including when no AP can be spent. It uses saved Wooden Yukari Dolls; it cannot purchase refills or spend premium currency.

Each tile is a separate transaction: recognize two consistent boards, select one tile, verify the 200-currency cost, persist intent before confirming, wait for the delayed reward overlay, record the receipt, and verify exactly one fewer closed tile and exactly 200 less currency. New intents preserve the complete pre-spend board and validated receipt checkpoints. Recovery runs before startup and accepts only the same saved receipt or an exact post-transaction board after its receipt was logged; it never repeats the spending input. Legacy intents, changed receipts, and interruptions during an unverified scroll or tooltip remain held for review. Completed prizes are remembered across visits. Refresh intent is also persisted, and a reset must advance exactly one round without changing currency.

The first implementation collects every prize in rounds 1–3. It does not discard remaining prizes just because Refresh becomes available. After completing round 3 it advances to round 4 without revealing a tile there; existing AP routing then observes that the three-round goal is complete. With insufficient currency it returns home and waits for the next visit. A visit has explicit action, tile, and time limits. Long visits yield between tiles after 40 minutes or 420 inputs, leaving room to finish the current receipt before the one-hour/600-input hard limit. Saved currency remains available for the next visit. Recovery can confirm a recognized free refresh only when a durable intent proves the prior board had no prizes left; other notices remain untouched.

Live development on September 30 completed round 1: all nine prizes in 37 reveals, costing 7,400 event currency. The board contained 31 prize cells and six revealed empty cells. Saved receipts and exact board deltas were reconciled without repeating spending after recognition fixes. The completed-board overlay and free refresh into round 2 were verified live; the balance stayed at 2,359 through the refresh. Round 2's different inventory was also verified in the client. Round 3 and the final round-goal transition remain covered offline rather than claimed as live-tested. Multi-item rewards use the shared item inspector; stable, fully visible single-item receipts use the guarded no-input fast path.

The subsequent daily run completed successfully, including another seven round-2 reveals. Across the first two round-2 visits, 11 reveals cost 2,200 currency and completed two prizes, leaving 159 currency. The AP follow-up swept event Quest 9 twice (40 AP), leaving 113 AP above the configured 100-AP floor. Its rewards raised treasure currency to 263; the automatic treasure follow-up opened another tile, verified its 40,000-credit receipt, and returned home with 63 currency. One earlier temporarily unidentified 40,000-credit receipt was reconciled from its saved image without repeating the transaction. Initial unreadable reward layouts now receive three bounded, read-only retries before remaining marked unidentified; foreground, freshness, and receipt identity checks still apply.

## Shops, receipts, and crash recovery

All resource-consuming actions follow the same transaction boundary:

`observe → plan → persist intent → revalidate → act once → verify → commit receipt/state`

An intent includes season/profile revision, action identity, stage/item/cell, quantity, expected cost, balances, and screenshot path. On restart, reconcile it against game state before any repeat. A timeout after Confirm is an ambiguous transaction, not permission to press Confirm again.

Shop purchases require recognized item, price, currency, quantity, stock, and selected target. Respect currency reserves and finite stock. Infinite credit sinks are off unless explicitly configured. Minigame rewards, shop purchases, and free event-task claims feed the shared loot reader, including tooltip inspection and icons. Log currency spent separately from loot received; moving currency through an exchange must not double-count it as newly acquired currency.

Record every sweep, claim, purchase, opened cell, board refresh, skipped target, and decision to stop. Keep full daily text logs and linked screenshots. Unknown loot stays inspectable; missing item identification does not fabricate a name or quantity. An isolated recognition failure backs off that task; an unresolved spend holds its resource domain until reconciled.

## Dashboard

Add an **Events** page with current and reward-only seasons. Show end times in local time and UTC, profile verification status, prerequisites, saved bonus coverage, AP policy/budget, and editable reward priorities. A dry-run plan lists stages, expected currencies, estimated AP, and unmet goals before enabling spending.

Offer the three-round and six-round presets for this event, plus custom targets. Explain that the treasure cost is uncertain. Show progress toward targets and the last action with a screenshot. Put manual inspection/retry controls under the existing collapsed manual controls; ordinary operation remains Pause/Resume and “do everything.”

## How the next event gets added

1. Read the current server's patch notes, event wiki, and farming walkthrough. Record sources, dates, rerun differences, currencies, costs, deadlines, and suggested priorities.
2. Select existing adapters. For a new mechanic, write a small adapter contract and tests before enabling its spending capability.
3. Capture the actual entrance, destination, stages, bonuses, shops, and mechanic screens. Sanitize account information in committed fixtures; preserve native-resolution OCR evidence.
4. Create a new season profile and validate all IDs, dates, references, bounds, and adapter compatibility. Reuse layouts only after matching current evidence.
5. Run offline recognition/planning tests and a read-only live survey. Compare the displayed plan with the researched goals.
6. Validate one authorized small sweep and exchange; for boards, validate a cell and an explicitly selected refresh boundary. Reconcile costs and loot before increasing batch sizes.
7. Publish the reviewed profile with its verification status. Installation uses the normal release/update path; no unattended remote profile download or runtime AI authoring.

A familiar event should mostly require new data and fixtures. A new minigame should require one reusable adapter, not another complete event job.

## Implementation and acceptance

Ship in increments: registry and read-only inspection; shared AP planning and sweeps; shops/free claims; Treasure Hunt. Enable only live-verified capabilities in the released profile. The implementation and live evidence below cover only the delivered subset of this larger design.

Required tests cover overlapping playable/reward windows, original versus rerun state, missing/rotating banners, locked stages, bonus rounding, partial budgets, expired profiles, shop stock and duplicate receipts. Add interruption tests before/after every spend, board refresh recovery, manual interference, queue fairness, and 720p/1440p OCR fixtures. Invalid or unsupported profiles must fail before input.

Completion means the configured goals progress without crossing the AP floor or budgets; each spend has durable evidence; retries cannot duplicate it; expired farming stops while eligible rewards remain collectible; and ordinary AP work resumes when the event no longer needs it. A later event using existing mechanics must be addable through a reviewed profile without rewriting the runner.

### Event identity and inspection

The Campaign carousel can open a concurrent event whose play period has ended but whose reward shop remains available. Inspection uses the home banner, then verifies the Aquatic Showdown subtitle (or the title plus Sun-Kissed Beach quest), Quest tab, and Treasure Hunt entrance before proceeding. Expired event screens are regression fixtures and must not match. No resource-spending inputs exist in the inspector.

The optional `[ap].event_priority` setting reserves AP while this event is playable, regardless of the ordinary farming strategy. It is off by default. When enabled, the AP runner clears unfinished Stories first, then checks the treasure round before repeatable quest or ordinary farming. Through round 3 it selects the first available three-star quest in the reviewed order 9 → 5 → 1 and projects a sweep down to the shared AP floor. A missing clear or uncertain screen holds normal farming; it never silently falls through. Normal farming resumes only after observing a round above 3, when the event expires, or when the setting is disabled. The adapter has live sweep evidence on Quest 5. The separate opt-in Treasure Hunt task spends saved treasure currency and advances completed rounds.

Live validation on 2026-09-29 completed an eight-input inspection and returned home: Quest 1 had zero stars and Treasure Hunt was on round 1; AP remained 220/220. The home banner can also rotate into recruitment. The inspector recognizes that wrong destination, returns home, and retries at most three entries. This verifies navigation and prerequisite inspection only, not event farming or treasure spending.

### One-time quest clearing

A reviewed profile in its playable date window exposes the dashboard's yes/no prompt, even when event farming is disabled. It says “event available” until in-game inspection verifies the event identity. Yes runs `restart → clear_event` before other queued work. The clear visits quests 1–12, skips verified three-star clears, uses Quick Formation → Auto for every new battle, and enables battle Auto when needed. It records the formation, result, itemized reward receipt, and post-battle stars. A result below three stars, insufficient AP above the configured floor, or the end of the quest list stops the job. Unknown results fail without replaying the battle.

After success or failure, dispatch pauses and the existing queue stays intact. No red-dot follow-up or treasure input runs afterward. No dismisses the prompt for this season without touching the device. First-clear consent is separate from enabling automatic event farming. The local state records a pending battle before Mobilize, so an interruption cannot silently spend AP again.

Tests cover actual sanitized 1440p screens, quest-number/row matching, per-quest Auto Formation, stopping below three stars, pending-intent guards, event-priority holds, queue preservation, and pause-after-clear behavior. Live validation on September 29 verified Quests 1–8 at three stars using Auto Formation. Quest 9 finished in 2:11 with two stars: every student survived, but the 120-second objective was missed. Clearing stopped there, returned home, and left dispatch paused with 115 AP. All nine reward receipts were recorded. The subsequent event sweep validation is recorded below; the independent Treasure Hunt implementation is described above.

Quest 9 also exposed a receipt input whose screenshot expired during the ADB preflight. No input had been sent. The reader now makes at most three attempts, each requiring fresh validation of the same receipt; changed screens still stop the job. The interrupted receipt was recovered and its stars verified without replaying the battle. Post-battle star screenshots are now saved alongside reward evidence.

### Event sweep recognition and live validation

On September 29, the full-screen OCR omitted the small sweep quantity at 1440p:
it rejected `1` at low confidence and missed `49` entirely. This first produced a
misleading insufficient-AP summary, then caused quantity adjustment to time out.
An unreadable cost now waits for the same identified three-star event stage,
instead of treating it as insufficient AP or falling through to normal farming.
Missing quantities are reread from a native-resolution crop at two scales; both
reads must agree with high confidence. The projected AP and final confirmation
remain independent checks before any spend.

The live retry skipped two-star Quest 9 and swept three-star Quest 5 **43 times**
at **15 AP each**, spending **645 AP** and taking the balance from **758 to 113**.
The configured **100 AP floor** was preserved. The game displayed the matching
sweep result and event-currency rewards. No Hard-stage sweep or AP purchase was
part of this visit. Sanitized 1440p fixtures cover the missing `1` and `49` reads,
and regression tests preserve the event reservation when cost recognition fails.

This visit also exposed a separate receipt-pagination limitation: the loot reader
could not prove overlap between two Full List pages. It saved both screenshots
and the partially identified drops for review. The sweep result and final AP
balance were verified, the pending transaction cleared, and the job returned
home successfully. Incomplete itemization does not authorize repeating a sweep.

The saved Full List pages now pass an overlap regression. Fractional scrolling
changed the blueprints' antialiasing enough to exceed the original pixel tolerance.
The comparison allows that measured variation while still requiring matching
quantities, card dimensions, rarity, column alignment, and multiple ordered cards.
Changed quantities, substituted artwork, and reordered cards remain rejected.
This is saved-screen validation; the original partial receipt remains partial,
and no sweep was repeated to reconstruct missing item names.

On September 30, Quest 9 was observed with three stars and swept 44 times at
20 AP each: **880 AP**, taking **990 to 110** with the 100 AP floor preserved.
A larger Full List receipt exposed a separate, marginal scroll-overlap mismatch.
Sanitized before/after fixtures now verify that six unchanged cards survive a
uniform 32-pixel scroll, while altered quantities, artwork, ordering, and ambiguous
overlaps are still rejected. This correction is limited to scrolling comparison;
it does not relax receipt identity checks before input. The original partially
itemized receipt remains incomplete because later rows were never captured.


### Story-first AP spending

Aquatic Showdown has 12 Story stages, each costing 10 AP, as listed in the
[rerun stage table](https://bluearchive.wiki/wiki/A_Flower_Blooms_Among_The_Hundred_%EF%BD%9E_Honorable_Sea_Showdown_%EF%BD%9E/Rerun).
The adapter opens the Story tab and checks numbered rows in order. Narrative
stages use the game's Menu → Skip → Confirm flow. Battle stages retain a locked
guest team when the game supplies one; otherwise they use default Auto Formation.
Battles use Auto, and rewards enter the ordinary loot ledger.

Every paid entry requires a verified AP projection and keeps the configured
floor. Unfinished stories hold quest and normal farming, even when the treasure
round goal has already been met. A bounded visit may defer remaining stories
until the next AP check. Confirmed progress survives daemon restarts and resets
with the event season; an unresolved paid entry blocks replay until its saved
receipt and completion are reconciled. These routes remain deterministic and
require no AI during normal operation.

Live validation on September 30 cleared Stories 1–4 for **40 AP total** and
returned home with **103 AP**, preserving the 100 AP floor. Story 2 used the
game's locked guest team and Auto battle; the narrative stages used the skip
flow. Reward receipts and gold completion markers verified each clear, and
progress persisted with no unresolved entry. Stories 5–12 remain for later
visits as AP becomes available; their individual layouts have not yet been
live-verified. The editable Auto Formation path has offline control-flow tests;
the later battle episode still needs its own live validation.
After restarting the daemon, its scheduled visit resumed at Story 5, verified
the 10 AP cost, and left the episode unstarted to preserve the floor. It returned
home without falling through to event quests or normal Hard farming.
