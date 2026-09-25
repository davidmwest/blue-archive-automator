# Total Assault

pick the difficulty you want in **settings → total assault**, then **queue total assault**. Hardcore is the default. including it in Daily is a separate switch, off by default; when enabled it runs after Lessons and before AP spending and Tasks collection. this changes the manually queued Daily plan, not a nightly schedule. the café timer doesn't run it.

the plan is to prove the team works before using a real ticket:

1. inspect the active raid and the chosen difficulty. if easier clears are required to unlock it, work through the observed prerequisites first. don't go above the selected difficulty.
2. use **Mock Battle**, let the game's auto formation build the team, and run the fight with auto skills.
3. a winning mock needs a readable victory result and enough time left. **seconds to spare** defaults to 30. an unreadable result doesn't count as a pass.
4. if that team doesn't pass, read the damage breakdown and replace the lowest-damage **striker** with an available assistant striker of the matching attack type. compare the eligible assistant list by stars, then level; don't replace a special student or pick an arbitrary first row.
5. run another mock with the assistant team. it must pass the same checks. a better-looking formation alone doesn't authorize a real attempt.
6. use the exact team that passed for the real clear, verify the result and ticket change, then sweep the remaining available tickets at the target difficulty once the game permits it.

if no eligible assistant exists, the assistant mock fails, or the screen can't prove what happened, stop and leave a failed-job notice. the player can choose a lower difficulty or do the fight manually. the job doesn't silently lower the target or buy tickets. a real attempt whose result is uncertain needs review before another ticket can be spent.

the difficulty setting accepts Normal, Hard, Very Hard, Hardcore, Extreme, Insane, Torment, and Lunatic. having an option in the list doesn't promise that every raid or difficulty can be handled automatically. unsupported mechanics, unreadable roster details, and unverified formations must stop before real entry. the current assistant reader requires a consistent attack type across the auto-selected strikers and a fully observed eligible offering; a mixed-type team stops for review.

```toml
[total_assault]
difficulty = "hardcore"
enabled_in_daily = false
comfort_seconds = 30
```

CLI: `ba --config config/local.toml total_assault`. the queue uses an immutable settings snapshot and the existing per-instance lock, logs its decisions in the daily text log, and records verified rewards in Loot Gathered. configuring this task adds no AI dependency.

## Rules that affect the job

the [official Global Total Assault guide](https://forum.nexon.com/bluearchive-en/board_view?board=3222&thread=2523158), checked September 25, 2026, describes these rules:

- three tickets recharge at 19:00 UTC each day, with a holding limit of six. opening a real boss attempt costs one ticket; its room lasts one hour.
- seasonal **Mock Battle** costs no tickets and awards no battle rewards. it is separate from the all-boss **Mock Total Assault** mode.
- sweeping requires a real clear on the same game day.
- assistants come from friends, club members, or general users. general-user loans cost more credits. only one assistant can be borrowed for a boss attempt, and the formation cannot contain duplicate students.
- a failed real attempt can still award preservation rewards. coins or ranking points alone do not prove victory.

the current real-entry reader recognizes the captured **40,000-credit** assistant confirmation. it verifies the selected assistant and persisted entry intent before confirming once. a different or unreadable fee stops the job. sending Confirm is logged separately from proving a deduction or battle result.

locked difficulties also matter. the captured staging menu shows Very Hard, Hardcore, and Extreme available; it does not demonstrate an unlock progression. for an account with a locked target, the job reads the observed locks, clears an available prerequisite, then checks again. each real prerequisite uses a ticket, so a run may make progress without reaching the target if tickets run out. a mock clear cannot stand in for an observed unlock. prerequisite planning is covered by offline tests; its live flow has not yet been validated here.

the official guide's [locked-difficulty example](https://dszw1qtcnsa5e.cloudfront.net/community/20250123/4e89f71c-2dde-4cf4-ac24-750e4f721edb/image.png) shows a padlock and “Unlocks from clearing the lower difficulty.” the reader recognizes that exact explanation within its difficulty row, as well as an explicit **Locked** label. the guide image is historical, not a local staging fixture; the phrase matcher has offline coverage. an icon-only lock or conflicting lock and Enter evidence stops planning.

## What counts as a comfortable win

the result reader needs a recognized victory layout and a readable battle time. a zero boss-HP reading, a generic completion heading, or a reward receipt on its own is insufficient. the result screen's time is **elapsed game time**; emulator speed and wall-clock duration do not determine the margin.

Drumbarka has a 4:30 battle limit. the [raid calculator maintained by joexyz](https://ba.joexyz.online/raid-score-calculator) documents this limit, and the staging battle timer provides the local check. a result of `04:29.667` therefore leaves `00:00.333`, which fails the default 30-second requirement even though the boss was defeated.

the result portraits and their thin bars are not reliable evidence of how many strikers survived. the reader leaves survival unknown unless it has explicit evidence. a verified win with enough time qualifies when survival is unknown; an explicitly observed retired striker disqualifies it. “comfortable” currently means this time margin, not a guarantee that repeated auto battles will have identical outcomes.

a passing mock creates a single-use qualification for the exact event, boss, difficulty, game day, team, and assistant offering. it expires after 15 minutes and belongs to the current run. before a real entry or sweep, the job saves its intended ticket expenditure. it then checks the result and ticket decrement. an interruption or ambiguous result leaves that action unresolved and blocks another spend until reviewed.

assistant identity comes from the visible lender name, student, stars, and level. the job rechecks those fields when restoring a borrowed slot for real entry. the game screen does not expose a unique lender ID, so otherwise identical offerings from lenders with the same displayed name cannot be distinguished.

## Validation status

the following is the September 25, 2026 staging checkpoint. this is staged live validation: recognition and transition fixes were added between steps. a fresh uninterrupted run of the finished daemon has not yet been verified. offline tests are separate from live validation; a passing policy test doesn't establish that a boss can be beaten.

| Area | Evidence so far |
| --- | --- |
| Configuration and integration | Offline coverage for settings validation, TOML snapshots, CLI startup gating, optional daily ordering, and dashboard queue integration. |
| Planning and spending guards | Offline coverage for observed difficulty locks, assistant choice, exact-team qualification, expiry, persisted ticket intents, and blocking ambiguous or repeated spending. |
| Screen recognition | Sanitized local captures cover the menu, difficulty details, formation, assistant selection and fee, entry confirmation, auto controls, battle timer, damage report, winning result, real reward receipt, season-record notice, sweep confirmation, sweep receipt, and zero-ticket detail. The tested fallback starts from a recognized victory with insufficient time left and a complete striker damage report. A guarded loss recognizer has offline coverage, but an actual defeated or timed-out mock's result and recovery flow have not been captured locally. An unrecognized result stops the job rather than guessing its outcome or authorizing a ticket. Captured layouts do not establish support for every boss or language. |
| First Hardcore mock | The auto team defeated Drumbarka in `04:29.667`, leaving **0.333 seconds**. It did **not** qualify for real entry at the default margin. The damage report was read; no survivor count was inferred. |
| Assistant Hardcore mock | Replacing the lowest-damage striker with the verified **5★, level 90 Aris (Armed)** assistant produced a live win in `01:34.233`, leaving **175.767 seconds**. AUTO was visibly enabled, and all six students' damage values were verified. This passed the default comfort margin. The live fallback began with the first mock's narrow victory; it did not test an actual defeat or timeout. |
| Prerequisite real clears | Hardcore was already unlocked on the staging account. Prerequisite discovery and planning have research and offline coverage only; no live unlock sequence was exercised. |
| Target real clear | The same verified assistant team defeated Hardcore Drumbarka live in `01:31.300`, leaving **178.700 seconds**. Before entry, the borrowed slot reset to **EMPTY** and the matching assistant offering was restored and verified. The **40,000-credit** assistant confirmation and separate one-ticket confirmation were handled; fee deduction has not been independently reconciled. The real reward receipt contained **100 Total Assault Coins and 10 Advanced Total Assault Coins**. The observed ticket count changed **6 → 5**. The actual post-battle path was Reward Acquired → Best Season Record Reached → menu. |
| Remaining-ticket sweeps | Five Hardcore sweeps were confirmed once. The receipt's **Final** row contained **500 Total Assault Coins and 50 Advanced Total Assault Coins**; per-sweep rows were not counted again. The job returned to Hardcore detail, verified **0 tickets**, reconciled the **5 → 0** expenditure, and cleared the pending spend. Total verified raid loot was **600 Total Assault Coins and 60 Advanced Total Assault Coins** across the real clear and sweeps. Return home was verified, the run finished successfully, and the instance lock was released. |
| Adjacent Tactical Challenge receipt recovery | This visit collected **22,560 credits** from Time Reward. The previously claimed daily receipt was then confirmed as **18 Pyroxenes and 70 Tactical Challenge Coins**, logged, and returned home. Daily receipt inspection needed recovery after a freshness timeout; the resulting fix has regression coverage. This is not evidence of a Total Assault clear or sweep. |

the [fixture provenance notes](../tests/fixtures/README.md) describe which screenshots are captured, cropped, or synthetic. the fixtures use game imagery and have the [game-asset license exclusion](../THIRD_PARTY_NOTICES.md), separate from the original code's MIT license.
