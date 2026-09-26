# Tactical Challenge

**collect tactical rewards** checks the Time Reward and Daily Reward buttons, claims whichever are available, logs the receipts, and heads home. it's also part of Daily. the game can start accumulating time credits again immediately, so each reward gets one claim per visit.

**climb tactical challenge** is the separate battle job. it uses the saved formation and spends existing tickets down to your reserve: one ticket for manual play by default, so four of a fresh five-ticket balance can be automated. reward red dots only queue the reward collector.

## ladder strategy

scout the opponent list using free refreshes and keep every fully verified candidate ahead of your rank. compare a weighted team-level score:

```text
estimated team levels = sum(visible levels) + hidden slots * player level
account multiplier = 2 when player level is 90 or higher; otherwise player level / 90
weighted score = estimated team levels * account multiplier
```

lower scores come first. level-90+ accounts get a deliberate extra penalty: the multiplier jumps to 2. this is a rough strength estimate, not a measurement of relationship bonuses or battle outcome. hidden units use the player's level, rather than counting as zero. equal scores use a random tie break. a readable rank alone is enough for the sampling estimate below, but it does not make an opponent eligible: selection also needs verified identity and visible student levels. the runtime makes these decisions locally; no AI is involved.

**never fight the same opponent twice in one game day.** a win, loss, or recovered result all count. the history persists across visits and server restarts, so **do everything** cannot rematch someone who was already challenged. at the next 19:00 UTC reset, completed opponent history clears. an unresolved ticket expenditure remains held for review across reset rather than being blindly replayed.

a win triggers a fresh survey at the new player rank. before opening formation, the opponent detail must still show the same identity and visible team levels, an opponent ahead of you, your unchanged rank, and exactly one projected ticket spent. a changed game day invalidates the pending selection too. stop at the ticket reserve and never buy tickets.

keep every occupied slot in the saved attacking formation. only fill blanks, using the highest-level eligible owned students for that role. both Striker and Special slots must be complete before entry. missing or ambiguous roster evidence stops the job.

battle animations are skipped by default. turn **skip battle animations** off to watch the full fight. both modes spend the same ticket and require a result plus the expected ticket decrement before recording completion. the game imposes standby time between battles; the runner waits for the displayed countdown.

## what the scouting estimate means

the initial sample is **50 valid refreshed lists** by default. each list has three rank positions. the model treats each row as drawing uniformly from its own fixed pool, independently over successive refreshes; the rows do not need to be independent of each other. observed rank positions and repeat sightings estimate the size of those pools. a larger, conservative upper bound sets the remaining sampling budget and a later search budget for the chosen opponent.

**the selected confidence is conditional on that model, not a guarantee about Blue Archive.** the default is 99%; choose 90% for a shorter search, or any value from 80% to 99.9%. a lower setting allows a greater chance of unseen opponents under the model. the game does not publish the sampling probabilities. our saved screens show different rank ranges in the three rows, which is why they are modeled separately. a changing ladder, weighted selection, or refreshes that did not actually occur can invalidate the estimate. the claim concerns coverage of eligible rank positions; it does not prove that every team's level or identity was readable.

only an observed refresh with all three readable, distinct rank labels supplies a draw. an unreadable or duplicate rank label ends that survey, rather than silently dropping a potentially biased sample. team OCR failures do not remove otherwise valid rank samples. the initial screen and repeated screenshots of one refresh are not extra draws. the displayed refresh countdown must visibly reset to acknowledge the input, so an acknowledged redraw of the same list remains a valid repeat. an unacknowledged refresh ends the survey without a confidence claim or battle entry.

pool bounds are calculated at predetermined checkpoints, starting at the configured sample size and doubling. this avoids repeatedly fitting until the result happens to look favorable. the remaining error allowance (1% at the default 99% confidence) is divided between pool estimation, coverage, and one chosen-opponent lookup. **this confidence applies to one survey and its lookup, not the entire job or day.** a fresh survey after a rank change starts a new estimate; the implementation does not claim a combined 99% success probability across those searches. the implementation uses the [occupancy distribution](https://arxiv.org/abs/2209.02220) for the bounds and a [union bound](https://www.cl.cam.ac.uk/teaching/2122/RandAlgthm/lec1_intro.pdf) for the chance of unseen positions.

long searches run in chunks of **five minutes or 100 refreshes**, then return home and let the other jobs run. the local daemon resumes the saved survey or opponent lookup after at least a minute. it saves the original rank draws and replays the same statistical calculation when loading them; restarting the daemon does not manufacture a completed survey. each survey and target lookup is capped at 1,000 refreshes. each visit still has a 30-minute hard limit, with at least 12 minutes reserved before entering a battle for combat and result handling.

unfinished surveys keep their tickets and log their progress without claiming the selected confidence. saved searches expire after four hours; a changed game day, player rank, confidence setting, or initial sample size also discards them. pausing the queue pauses continuations, and turning off automatic Tactical Challenge stops them. rank positions can change hands while the queue does other work, so the stable-pool assumption still applies and the exact opponent is checked again before entry. if a chosen opponent cannot be found within the supported lookup budget, start a fresh survey. this is a conservative stale-selection policy, not proof that the opponent moved.

## settings and queue

```toml
[tactical_battles]
enabled_in_daily = true
skip_battles = true # false watches the full battle
preserve_tickets = 1 # 0..5
refresh_limit = 50 # 2..100 valid refreshed lists before the first estimate
confidence_percent = 99.0 # 80..99.9; try 90 for a shorter search
```

these settings are also under **settings → tactical challenge**. `refresh_limit` is the initial sample size, not the maximum number of refreshes for the visit. Daily fights before collecting Tactical Challenge rewards, so the reward claim uses the achieved rank. **do everything** also queues this job when enabled; it still respects the reserve and opponent history if Daily has already run. the periodic red-dot check never queues combat. an explicit manual run uses `ba --config config/local.toml tactical_battles`.

## reward collection

the route is home → Campaign → Tactical Challenge. the runner recognizes the page heading, Season, both reward labels, both Claim controls, and the ticket counter. only yellow Claim buttons authorize collection. gray buttons are skipped. each claim must produce a Reward Acquired receipt; an unknown screen or missing receipt stops the job instead of retrying the claim. tickets must stay unchanged before returning home.

receipts appear in Important Actions and Loot Gathered, with retained evidence for review. time credits are counted from the actual receipt, not the rounded total displayed on the menu.

## validation

September 24, 2026: the live staging account collected **70,570 credits** from Time Reward, verified the receipt, returned home, and kept **5/5 battle tickets**. both claim buttons were disabled afterward. a second complete restart → collection → home run collected 240 credits that had accumulated since, again retaining all five tickets. Daily Reward was unavailable during those runs.

September 25 UTC: another visit collected **22,560 credits**, then claimed Daily Reward. automatic inspection stopped at a stale-frame guard. recovery verified the existing receipt as **18 Pyroxenes and 70 Tactical Challenge Coins**, recorded those rewards, and returned home without claiming again. paired sanitized captures now cover animated-icon freshness checks. the receipt-reader fix passed offline regression tests; this recovered visit is not an uninterrupted end-to-end run of the corrected daily-claim path.

sanitized fixtures remove account and opponent identities. regression tests cover separate time/daily controls, dimmed screens, unavailable rewards, credits accumulating after collection, failure without blind retries, unchanged tickets, and separation between reward-only dispatch and ticket-spending battle dispatch.

September 26: one live battle with animations enabled completed as a loss. the post-battle Tip screen required recovery, after which the ticket balance was verified changing from **5 to 4** and the result was retained for same-day opponent exclusion. this was a staged check, not an uninterrupted run of the final implementation. this verifies the unskipped loss path; a live skipped completion and a live victory remain pending.

the corrected refresh acknowledgement passed three actual refreshes: each countdown reset was observed, all three rank labels were readable, and the ticket balance stayed at **4**. two lists had no fully readable teams and were excluded from opponent selection while still supplying valid rank samples. the third included verified candidates. this validates the refresh acknowledgement, not completion of the statistical coverage model or its lookup budget. skipped battle completion and victory recognition remain unverified live; the WIN counterpart has explicitly synthetic regression coverage.

two short live survey visits then verified persistence: the first saved three acknowledged refreshes and returned home; the second loaded those observations, reached six cumulative draws, retained the original checkpoint age, and returned home again. both kept all four remaining tickets. these visits used a three-refresh test chunk limit with battle entry prohibited; they validate checkpoint and navigation behavior, not completion of a full survey.

offline tests also cover the conditional confidence estimate, settings, both skip modes, entry guards, formation preservation, the shared visit limit, interrupted result recovery, and returning home without spending when insufficient budget remains. the earlier reward checks do not establish battle coverage.
