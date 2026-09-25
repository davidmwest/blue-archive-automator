# collect the red dots

when a job gets back home, check the home screen and Campaign for two stable views of the notification dots. queue the matching jobs, one at a time:

| notification | job |
| --- | --- |
| Buy Pyroxene | free daily pack, then mail |
| Social | Club check-in, then mail |
| Mail | collect product and ordinary mail |
| Tasks | completed task rewards and the daily completion bonus |
| Total Assault in Campaign | available rank and total-points rewards |
| Tactical Challenge in Campaign | time and daily rewards |

there's a **collect red dots** button for a manual check, and **collect free pack** for the package alone. the scanner doesn't keep the game open or wake an idle emulator to poll. Cafe and the other scheduled visits give it chances to check.

free means free. the package handler recognizes **Free Daily Pack** and the separate **Cost: Free** confirmation. it cannot operate Google Play billing. paid renewals keep their own opt-in settings. missing labels, an unexpected screen, or a failed receipt stop the job and leave local screenshots.

requests enter the normal serial queue. already queued work isn't duplicated. each associated task gets one attempt per busy queue batch, so a stubborn dot cannot keep the queue alive forever. later visits can check again. free packs and Club explicitly run Mail afterward, even if another job checked Mail earlier in the batch.

Campaign badges only request reward collection; they never start battles or enable Total Assault combat. amber availability markers don't count as red dots. the scanner returns home before publishing its requests.

Social can flag something other than attendance. the Club handler checks the Club card's own dot, then uses a per-instance checkpoint for the 19:00 UTC game day. it doesn't touch Friends, Assistant, or chat.

## AP after collection

a fresh home AP reading at least **20 AP above the configured floor** queues Spend AP immediately when automatic spending is on. it doesn't wait for the normal AP timer. the spending job reads each stage's actual cost and stops before crossing the floor; insufficient tickets or available stages can leave AP above the floor. the queue avoids duplicate spending jobs and only retries within a batch after later rewards raise observed AP by at least 20 in total. it honors pending-spend and failure holds, and never uses the manual retry override. unreadable or inconsistent AP readings do not authorize a follow-up.

## live verification

September 24, 2026, English 1280×720 staging instance:

- Social → Club displayed the attendance notice sending 10 AP to mail. that notice was saved, and Mail subsequently verified the 10 AP receipt.
- the Free Daily Pack granted **10 AP and 10,000 credits immediately**. its reward receipt was saved and added to Loot Gathered. the card then showed zero daily purchases remaining.
- Mail collected the Club reward, three defense-reward messages totaling 9 Tactical Challenge Coins, and 20,000 credits. both mailbox tabs ended empty.
- sanitized fixtures cover the real badge, confirmation, exhausted-card, attendance, and receipt screens. automated checks also cover missing/paid prices, dimmed overlays, same-day suppression, failed receipt handling, queue deduplication, and AP failure holds.

only received reward receipts enter Loot Gathered. a request to claim, a Club attendance notice, or a package-delivery notice is an important action but isn't counted as received loot.
