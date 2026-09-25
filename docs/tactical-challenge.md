# Tactical Challenge rewards

**collect tactical rewards** checks the Time Reward and Daily Reward buttons, claims whichever are available, logs the receipts, and heads home. it's also part of Daily. the game can start accumulating time credits again immediately, so each reward gets one claim per visit.

this job doesn't fight. the battle entry point is a no-input placeholder and isn't available to the dispatcher. opponent selection, team rules, and ticket spending come later.

the route is home → Campaign → Tactical Challenge. the runner recognizes the page heading, Season, both reward labels, both Claim controls, and the ticket counter. only yellow Claim buttons authorize collection. gray buttons are skipped. each claim must produce a Reward Acquired receipt; an unknown screen or missing receipt stops the job instead of retrying the claim. tickets must stay unchanged before returning home.

receipts appear in Important Actions and Loot Gathered. time credits are counted from the actual receipt, not the rounded total displayed on the menu. daily receipts retain their image for review until that branch has been verified live.

## validation

September 24, 2026: the live staging account collected **70,570 credits** from Time Reward, verified the receipt, returned home, and kept **5/5 battle tickets**. both claim buttons were disabled afterward. a second complete restart → collection → home run collected 240 credits that had accumulated since, again retaining all five tickets. Daily Reward was unavailable with a countdown, so its enabled-button behavior and claim flow are currently covered by offline tests, not a live daily claim.

sanitized fixtures remove account and opponent identities. regression tests cover separate time/daily controls, dimmed screens, unavailable rewards, credits accumulating after collection, failure without blind retries, unchanged tickets, and the battle stub's lack of device access.
