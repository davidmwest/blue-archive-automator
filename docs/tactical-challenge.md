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

each usable ticket gets its own **10 active minutes** before overtime, adjustable from 1 to 30 minutes, including fractions. this is a soft threshold for relaxing the search, not a limit on the whole Tactical Challenge job or a reason to leave a ticket unused. refresh speed varies with the emulator and OCR, so the journal reports a refresh-count estimate based on the speed actually observed.

1. spend the first 37% of the budget establishing a benchmark: **3 minutes 42 seconds** at the default.
2. take a currently visible opponent whose score is at least as good as the benchmark's best score.
3. gradually relax that threshold toward the weakest quarter of the benchmark population as the remaining time runs out.
4. after ten minutes, continue the same search in overtime. each unsuccessful acknowledged refresh loosens the threshold by one distinct observed score tier until a currently visible eligible opponent qualifies. reaching ten minutes never means fighting whoever happens to be on screen.

repeated opponents have one entry in the benchmark. later conflicting scores retain the higher estimate. the benchmark needs at least two readable list observations; unreadable teams consume time without inventing a score. there is no second search to relocate someone seen earlier: the selected opponent must be on the current screen and pass another check in their detail view.

this borrows the observation-then-selection idea from the [secretary problem](https://www.or.mist.i.u-tokyo.ac.jp/takeda/FreshmanCourse/Ferguson.pdf). repeated opponents, changing ranks, and the relaxed threshold make this a practical heuristic; it does not claim the classical rule's optimality or a 99% coverage guarantee. the former confidence and pool-estimation settings are retired from the runtime.

searches yield to other queued work after five minutes or 100 refreshes, including in overtime. the local daemon resumes after at least a minute, carrying forward elapsed time, the benchmark, and the relaxed score threshold. queue waiting and battle time do not consume search time. failures, cancelled continuations, and passive rank changes retain progress. a completed battle starts a fresh timer for the next usable ticket, whether it was a win or loss. only actual ticket spending reduces the number available above the manual reserve. legacy timeout records remain as audit history; they cannot make an unspent ticket unavailable. older expired search checkpoints resume in overtime with their elapsed time intact.

countdown resets acknowledge refreshes even when the same opponents reappear. unreadable refresh controls defer the search without input. if a fresh screen proves that a tap was ignored—the same opponent rows, three rank labels, player rank, tickets, and a naturally falling timer—the runner retries once within the current search. identical row images can prove an unchanged list even when student levels are unreadable; they cannot authorize a battle. ignored taps never count as new draws. a second ignored tap, an ambiguous reset, or persistently unreadable rank labels records a failure and allows up to three delayed retries per game day, 15 minutes apart, preserving the current search progress. transport errors and unresolved battles do not automatically replay. other queued work can continue.

the game may leave the old opponent list visible under **Now Loading...** after a refresh. the runner waits without sending another tap until that overlay disappears. delayed responses still need an independently verified timer reset; responses arriving after the old timer could have expired remain ambiguous.

active search evidence expires after four hours and blocks that search while preserving its progress, including overtime. the daily reset starts a new game day. changing the search-time setting resets the current benchmark, while completed battle history remains separate. rank changes retain the benchmark; every candidate must still be ahead of the freshly observed rank. pausing the queue or disabling automatic Tactical Challenge stops continuation dispatch.

## entering and finishing

before formation, the detail must still show the selected identity and visible team levels, an opponent ahead of you, your unchanged rank, and exactly one projected ticket spent. a changed game day also invalidates entry. when the game rejects an opponent, back out and start a fresh search with a full timer; retain the day's completed battle history and ticket reserve. stop at the reserve and never buy tickets.

an otherwise verified opponent preview with unreadable student levels follows that same rejection path. its header, information panels, formation button, player rank, and ticket projection must identify the screen before the runner closes it. uncertain levels never authorize battle entry.

a preview portrait can shift slightly between the list and detail. if the ordinary comparison fails, the runner checks translations within two canonical pixels using a stricter 0.95 correlation threshold. the normalized name and account level must still match exactly, and all team, rank, ticket, and reserve checks still apply. accepted corrections are recorded in the journal.

keep every occupied slot in the saved attacking formation. only fill blanks, using the highest-level eligible owned students for each role. all Striker and Special slots must be complete. ambiguous roster evidence stops entry.

battle animations are skipped by default. turn **skip battle animations** off to watch. both modes require a result and the expected ticket decrement before recording completion. the runner waits through the game's standby countdown between matches. each visit has a 30-minute limit and reserves 12 minutes of headroom before battle entry for combat and result handling.

if a battle is interrupted, its persisted entry prevents another ticket from being spent. retrying **climb tactical challenge** checks the current result before restarting or navigating away, saves the result screenshot, then verifies the one-ticket decrement. an unknown result stays held for inspection. compact skipped-win and skipped-loss dialogs and the full battle result are recognized.

## settings and queue

```toml
[tactical_battles]
enabled_in_daily = true
skip_battles = true
preserve_tickets = 1 # 0..5
search_minutes = 10.0 # 1..30 active minutes per ticket before overtime
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

the previous per-ticket implementation was checked live by migrating an expired 600-second search and scheduling another visit. that release counted expired searches against daily opportunities. the current policy supersedes that accounting: reaching ten minutes continues the same ticket's search with gradually wider score tiers. the live overtime check below validates the replacement policy.

one live search stopped before entry because the list read a student's level as 38 while the detail read 88. the saved screens show 86/88/88. taller OCR crops now reject that conflicting list reading instead of giving the opponent a deceptively low score. sanitized list/detail fixtures cover the failure. a rejected or expired preview now closes and starts a fresh search after verifying the ticket count is unchanged, with no new battle intent.

a subsequent search selected a level-84 opponent with visible student levels 82/82/82, verified on both the saved list and detail screens. the battle lost with animations skipped. its compact LOSE dialog revealed another recognition gap; the corrected recognizer recovered that existing result, verified tickets 4 → 3, retained the opponent in the day's history, and immediately started a new 600-second search. the result fixture masks the account and opponent information behind the dialog. this verifies interrupted skipped-loss recovery; it does not claim uninterrupted completion of that match.

the following search saved its progress, yielded to a scheduled AP job, and resumed automatically. it selected a level-82 opponent with visible levels 78/78/82. that skipped battle completed without intervention: the loss was recognized, tickets reconciled from 3 → 2, and a fresh 600-second search began for the remaining usable ticket. the three previously fought opponents remained excluded.

the final usable ticket reached 600 seconds of active searching and entered overtime. acknowledged refreshes widened the score threshold from 1040 to 1080; a queue yield preserved that threshold, and the daemon resumed automatically. it selected a level-90 opponent with visible levels 90/88/90 and score 1076, verified on both list and detail screenshots. the skipped loss completed without intervention, tickets changed from 2 → 1, and the runner returned home with no pending battle or search. the one-ticket manual reserve stopped further entry. the reward check afterward exposed a separate navigation failure: the reward collector repeated the entry tap while Campaign lingered during loading, accidentally opening an opponent preview. it now sends one entry tap and waits read-only for up to 60 seconds; delayed-transition tests verify that the coordinate is never repeated. a subsequent live reward-only retry collected 9,900 credits, verified the ticket balance stayed at 1, and returned home. the fixed failure notices were then dismissed without removing their history.

unreadable student levels can still extend searching: later captures did not resolve ten of eleven inspected ambiguous lists. these candidates remain excluded rather than weakening the guards against 80 → 30 or 88 → 38 mistakes. the verified-layout rejection fallback and bounded recovery from a persistently black startup screen are covered offline; subsequent normal startups do not establish that the black-screen recovery ran live.

a September 27 native 1440p preview was rejected because its avatar shifted two canonical pixels despite matching name, account level, and visible team levels. sanitized saved screenshots reproduce the failure: portrait correlation rises from 0.639 to 0.986 after registration. tests retain the original aligned 1440p and 720p matches and reject changed identities, weak matches, and invalid entry conditions. this correction is verified offline; a live battle using the fallback remains unverified.

offline tests cover the time policy, overtime score tiers, continuation after the soft threshold, persistent elapsed time and relaxation, migration of older checkpoints, repeated identities, contradictory levels, settings, both skip modes, entry guards, formation preservation, result recovery, and ticket reserves. the earlier live sampling checks used the retired confidence strategy.
