# Event farming

**Status:** quest clearing, seasonal navigation, and optional event-first AP routing are implemented. Quest clears have live evidence; the event sweep adapter has offline coverage and still needs live validation. The greedy treasure planner exists, but treasure spending and event shops are not implemented.
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
- Story unlocks remain manual. Initial quest clears can be explicitly requested from the dashboard and use the game's default Auto Formation each time. Specialized bonus-team optimization remains future work.

The reviewed asset uses season ID `aquatic-showdown-global-2026-rerun`. Original-run shop and reward tables must not be copied without comparison. Navigation, early quest costs and results, and the treasure entrance have live evidence. Later stage costs, shop stock, and treasure spending still require observation; no guessed coordinates authorize a spend.

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

`ba_automator/treasure_policy.py` implements the pure planner. It is not yet wired to live board recognition or resource spending. Live inspection found round 1 with zero treasure currency, and no cleared event quests; no treasure click has been live-tested.

For every treasure, enumerate its rectangular placements within the board. Only permit rotations when the reviewed profile says they are allowed. Reject placements covering a confirmed empty cell or another treasure's revealed cell, or missing one of this treasure's revealed cells. Then enumerate non-overlapping full-board arrangements and retain only placements that occur in at least one legal arrangement. Unwanted and already completed treasures still constrain the available space.

For each unopened cell `c`, compute:

```
coverage(c) = number of distinct supported placements of desired treasures containing c
completion(c) = number of those placements whose only unopened cell is c
next cell = highest coverage, then highest completion, then row/column order
```

A placement counts once even if many arrangements of the other treasures support it. All clicks have the same currency cost, so coverage per click also maximizes coverage per unit of currency under this heuristic. This score is not a calibrated hit probability or a globally optimal spending strategy. It intentionally favors cells that test many hiding places at once, including overlapping possibilities for larger prizes. Confirmed hits constrain future placements naturally; we do not blindly open adjacent cells.

After one paid reveal, wait for the stable board, identify the revealed cell, reconcile currency, and rerun the planner. Never batch the resulting coordinates. If all selected treasures are fully revealed, the planner returns no move; the separate round controller verifies rewards before deciding whether to refresh. Reaching round 3 is not completing round 3: finish its selected rewards before releasing the AP priority hold.

Unreadable cells remain unknown, never empty. Contradictory observations or enumeration beyond the configured node budget produce `BoardUncertain`, with no proposed click. The caller must save evidence and request review/re-observe; it must not fall back to random spending. Round identity, currency checks, intent journaling, reward recognition, and reset authorization remain the executor's responsibility.

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

The optional `[ap].event_priority` setting reserves AP while this event is playable, regardless of the ordinary farming strategy. It is off by default. When enabled, the AP runner checks the treasure round before ordinary farming. Through round 3 it selects the first available three-star quest in the reviewed order 9 → 5 → 1 and projects a sweep down to the shared AP floor. A missing clear or uncertain screen holds normal farming; it never silently falls through. Normal farming resumes only after observing a round above 3, when the event expires, or when the setting is disabled. The adapter is implemented but event sweeps have not yet passed live validation. It does not spend treasure currency or advance rounds.

Live validation on 2026-09-29 completed an eight-input inspection and returned home: Quest 1 had zero stars and Treasure Hunt was on round 1; AP remained 220/220. The home banner can also rotate into recruitment. The inspector recognizes that wrong destination, returns home, and retries at most three entries. This verifies navigation and prerequisite inspection only, not event farming or treasure spending.

### One-time quest clearing

A successful event inspection exposes the dashboard's yes/no prompt. Yes runs `restart → clear_event` before other queued work. The clear visits quests 1–12, skips verified three-star clears, uses Quick Formation → Auto for every new battle, and enables battle Auto when needed. It records the formation, result, itemized reward receipt, and post-battle stars. A result below three stars, insufficient AP above the configured floor, or the end of the quest list stops the job. Unknown results fail without replaying the battle.

After success or failure, dispatch pauses and the existing queue stays intact. No red-dot follow-up or treasure input runs afterward. No dismisses the prompt for this season without touching the device. First-clear consent is separate from enabling automatic event farming. The local state records a pending battle before Mobilize, so an interruption cannot silently spend AP again.

Tests cover actual sanitized 1440p screens, quest-number/row matching, per-quest Auto Formation, stopping below three stars, pending-intent guards, event-priority holds, queue preservation, and pause-after-clear behavior. Live validation on September 29 verified Quests 1 and 2 at three stars with rewards recorded. Further results are recorded in the local task journal; event sweeps and treasure execution must not be inferred from those battle clears.
