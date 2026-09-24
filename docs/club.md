# Club check-in

open Social when it has a red dot, check the Club card, enter Club, and return home. the attendance reward is 10 AP sent to mail. the job collects mail afterward and logs what actually arrived.

Club stays immediately after restart in Daily and Cafe plans. standalone Club also starts with restart. the Social badge scanner can queue it independently. Friends and Assistant notifications don't authorize a Club visit: the Club card needs its own red dot.

attendance is checked once per configured instance and game day. the existing 19:00 UTC reset policy is calculated in `club.game_day()`, with boundary and timezone tests. a checkpoint is saved only after the Club page is verified. a visit without the reward notice is logged as a check, not a new reward. the actual AP is counted only from Mail's receipt.

unknown screens, membership prompts, and unreadable attendance notices stop for inspection. the job never writes chat messages or changes membership.

September 24, 2026: live validation observed the Club attendance popup, verified its 10 AP mailbox receipt, and returned home. same-day suppression and failure-before-checkpoint behavior are covered offline. see [red-dot dispatch](red-dots.md) for the shared queue behavior.
