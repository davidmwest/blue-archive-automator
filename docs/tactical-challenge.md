# Tactical Challenge

**collect tactical rewards** checks the Time Reward and Daily Reward buttons, claims what's available, logs the receipts, and heads home. it's also part of Daily. time credits can start accumulating again immediately, so each reward gets one claim per visit.

**climb tactical challenge** is the separate battle job. it uses your saved formation and spends existing tickets down to your reserve: one ticket for manual play by default. reward red dots only queue the collector.

## picking an opponent

compare teams ahead of your rank using this score:

```text
estimated team levels = sum(visible levels) + hidden slots * player level
account multiplier = 2 when player level is 90 or higher; otherwise player level / 90
weighted score = estimated team levels * account multiplier
```

lower scores come first. level-90+ accounts get an extra penalty because account level stops telling us much about their investment. hidden units count at the opponent's account level. this is a strength heuristic, not a prediction of who will win. exact score ties use a random choice. identity, rank, account level, and visible student levels all need to be readable before someone is eligible. contradictory level readings exclude the candidate instead of picking the friendlier number.

**never fight the same opponent twice in one game day.** wins and losses both count. history survives daemon restarts and **do everything**, then resets at 19:00 UTC. an unresolved battle stays held for review across reset rather than being replayed.

## ten minutes per ticket

each usable ticket gets one daily search allowance of **10 active minutes**, adjustable from 1 to 30 minutes, including fractions. with five tickets and a reserve of one, there are up to four allowances. refresh speed varies with the emulator and OCR, so time controls the budget. the journal reports a refresh-count estimate based on the speed actually observed.

1. spend the first 37% of the budget establishing a benchmark: **3 minutes 42 seconds** at the default.
2. take a currently visible opponent whose score is at least as good as the benchmark's best score.
3. gradually relax that threshold toward the weakest quarter of the benchmark population as the remaining time runs out.
4. if nobody visible qualifies at the deadline, keep the ticket and consume that search allowance. start a fresh search with the next daily allowance, if one remains. reaching the deadline never means fighting whoever happens to be on screen.

repeated opponents have one entry in the benchmark. later conflicting scores retain the higher estimate. the benchmark needs at least two readable list observations; unreadable teams consume time without inventing a score. there is no second search to relocate someone seen earlier: the selected opponent must be on the current screen and pass another check in their detail view.

this borrows the observation-then-selection idea from the [secretary problem](https://www.or.mist.i.u-tokyo.ac.jp/takeda/FreshmanCourse/Ferguson.pdf). repeated opponents, changing ranks, and the relaxed threshold make this a practical heuristic; it does not claim the classical rule's optimality or a 99% coverage guarantee. the former confidence and pool-estimation settings are retired from the runtime.

searches yield to other queued work after five minutes or 100 refreshes. the local daemon resumes after at least a minute, carrying forward elapsed time and the benchmark. queue waiting and battle time do not consume the search allowance. failures, cancelled continuations, and passive rank changes retain consumed time. a completed battle starts a fresh search for the next usable ticket, whether it was a win or loss. an unsuccessful search uses one allowance without spending its ticket, then continues with any remaining allowance. exhausted allowances stay counted for the game day across queue visits and daemon restarts, so a timeout cannot create unlimited searches. an exhausted checkpoint from an older release counts as one used allowance.

countdown resets acknowledge refreshes even when the same opponents reappear. unreadable refresh controls defer the search without input. if a fresh screen proves that a tap was ignored—the same opponent rows, three rank labels, player rank, tickets, and a naturally falling timer—the runner retries once within the remaining allowance. identical row images can prove an unchanged list even when student levels are unreadable; they cannot authorize a battle. ignored taps never count as new draws. a second ignored tap, an ambiguous reset, or persistently unreadable rank labels records a failure and allows up to three delayed retries per game day, 15 minutes apart, using the remaining search time. transport errors and unresolved battles do not automatically replay. other queued work can continue.

active search evidence expires after four hours and blocks that search while preserving the time already spent. the daily reset renews the search allowances. changing the search-time setting resets the current benchmark but retains the day's used allowances. rank changes retain the benchmark; every candidate must still be ahead of the freshly observed rank. completed battle history is separate. pausing the queue or disabling automatic Tactical Challenge stops continuation dispatch.

## entering and finishing

before formation, the detail must still show the selected identity and visible team levels, an opponent ahead of you, your unchanged rank, and exactly one projected ticket spent. a changed game day also invalidates entry. stop at the reserve and never buy tickets.

keep every occupied slot in the saved attacking formation. only fill blanks, using the highest-level eligible owned students for each role. all Striker and Special slots must be complete. ambiguous roster evidence stops entry.

battle animations are skipped by default. turn **skip battle animations** off to watch. both modes require a result and the expected ticket decrement before recording completion. the runner waits through the game's standby countdown between matches. each visit has a 30-minute limit and reserves 12 minutes of headroom before battle entry for combat and result handling.

if a battle is interrupted, its persisted entry prevents another ticket from being spent. retrying **climb tactical challenge** checks the current result before restarting or navigating away, saves the result screenshot, then verifies the one-ticket decrement. an unknown result stays held for inspection. both the compact skipped-victory dialog and the full battle result are recognized.

## settings and queue

```toml
[tactical_battles]
enabled_in_daily = true
skip_battles = true
preserve_tickets = 1 # 0..5
search_minutes = 10.0 # 1..30 active minutes per usable ticket
```

these are also under **settings → tactical challenge**. older `refresh_limit` and `confidence_percent` values remain readable for compatibility but no longer control search. Daily fights before collecting Tactical Challenge rewards. **do everything** queues battles when enabled and respects the reserve and daily history. periodic red-dot checks never queue combat. manual CLI entry is `ba --config config/local.toml tactical_battles`.

## rewards and evidence

the route is home → Campaign → Tactical Challenge. the runner sends one entry tap, then waits for the page to load; repeating that coordinate during loading can accidentally open an opponent. only yellow Claim buttons authorize reward collection. gray buttons are skipped. each claim requires a Reward Acquired receipt and unchanged tickets before returning home.

receipts appear in Important Actions and Loot Gathered. credits come from the actual receipt, not the rounded menu total. battle journals retain the selected list, opponent detail, and result screenshot, so level readings and ticket use can be reviewed.

## validation

live reward checks on September 24–25, 2026 collected time credits, verified receipts, returned home, and retained all five battle tickets. an interrupted Daily Reward claim was recovered from its existing receipt as 18 Pyroxenes and 70 Tactical Challenge Coins, without claiming again.

on September 26, an unskipped battle completed as a loss. recovery handled the post-battle Tip screen and verified the ticket balance changing from 5 to 4. this was a staged check, not an uninterrupted run of the final implementation. the noon Pacific reset then restored five tickets, explaining why the balance later appeared unchanged.

the corrected refresh acknowledgment completed a normal five-minute daemon visit with 22 verified refreshes, saved progress, returned home, and let the queue run its AP check. this covered slow OCR capture and the transient 2:01 countdown. a subsequent continuation exposed the duplicate Campaign-entry tap, now covered by sanitized loading and opponent-preview fixtures.

the new time policy completed a live daemon visit with 22 acknowledged refreshes in 4 minutes 51 seconds of active searching, crossed the 3-minute-42-second benchmark, saved 5 minutes 9 seconds of remaining search time, and returned home. observed throughput projected about 45 refreshes per ten minutes. this visit also verified the single-tap Campaign entry fix; all five tickets remained available because no opponent qualified during that portion of the search.

a continuation selected a level-75 opponent after 8 minutes 53 seconds of accumulated active search, with visible student levels 74, 72, and 65. the selected list and detail screenshots preserved those readings. the saved formation won with animations skipped: rank 571 → 541 and tickets 5 → 4. the initial runner did not recognize the compact WIN dialog; the corrected recognizer recovered it after a daemon reload, saved the result, reconciled exactly one spent ticket, and began the next search. this verifies interrupted skipped-victory recovery, not uninterrupted completion of that original run. the ignored-refresh and result-timeout notices were dismissed after their fixes were verified.

the per-ticket continuation fix was also checked live: the daemon migrated an older exhausted 600-second search, recorded one used allowance, and saved a fresh 600-second search with four tickets still present and one reserved. two search allowances remained. this verifies timeout migration and continuation scheduling; consecutive live timeouts have not been forced.

offline tests cover the time policy, per-ticket allowances, continuation after an unsuccessful search, deadline behavior, persistent consumed time, repeated identities, contradictory levels, settings, both skip modes, entry guards, formation preservation, result recovery, and ticket reserves. the earlier live sampling checks used the retired confidence strategy.
