# Daily packs and mail

Paid renewal is optional and **off by default**. Each permanent pack has its own switch and maximum USD price in Settings:

- Monthly Pyroxene Pack: default ceiling $6.99.
- Half Monthly Pyroxene Pack: default ceiling $2.99.
- 2-Week AP Pack: default ceiling $2.99.

These products grant daily benefits for their duration; this job does not buy them every day. It checks ownership once per Global game day and renews an enabled pack only when no active subscription or unclaimed purchase is visible. It waits through the entire remaining duration, including the game's early repurchase window. With all switches off, manually queuing **check packs + mail** only inspects ownership and collects mail. Limited Special Sale packs, including rotating Weekly AP offers, are outside this permanent-pack policy.

## Payment setup

Configure a Google Play payment method **inside the selected emulator** before enabling renewals. Signing into Blue Archive with Apple does not configure Google Play billing. The supported checkout is the observed English, USD Google Play screen on BlueStacks Air. Its payment sheet changes to 720 × 1280 portrait; game navigation remains fixed at 1280 × 720. Unknown layouts stop the job.

The runner checks the exact product and price in the game and again in Google Play, then sends one purchase confirmation. It does not store passwords, payment details, or screenshots of Play's payment screens. A recognized offer to enable backup payment methods is dismissed without changing account settings.

Google Play or the payment provider can require verification even when earlier purchases worked. The runner stops for payment setup, password/biometric verification, declines, unknown payment screens, or any uncertain result. See [Google Play purchase verification](https://support.google.com/googleplay/answer/1626831).

## Failure and recovery

Any failed pack job disables its automatic schedule and appears in the dashboard's **failed jobs** box. Notices survive server restarts; the box is hidden when there are none. Dismissing a notice or resuming the general queue does not clear the purchase hold.

A charge intent is saved atomically before pressing Google's purchase button. If execution is interrupted, the next manual pack check must observe an active pack or an unclaimed purchased pack to reconcile that intent. If ownership remains unclear, it refuses another charge. Check the game's product mailbox and Google Play purchase history before resolving an uncertain transaction. Do not delete pending state to make a charge retry.

After a failure that never reached a charge, fix the payment/setup issue and explicitly queue a pack check to retry (CLI: `ba packs --retry-packs`). Daily and scheduled execution cannot clear a payment hold. Selected renewal preferences are retained, but scheduled retries remain blocked until that manual check succeeds. A successful check schedules the next visit just after the 19:00 UTC game reset. Jobs use the same serial queue and instance lock as other routines.

## Collect mail

`packs` runs **restart → packs → mail**. `mail` is also a standalone queue job. Daily includes mail, with a pack check immediately before it when any paid renewal is enabled.

Mail visits Product first to activate purchased packs, then Unclaimed. It claims individual entries, handles the explicit claim confirmation, reads the reward receipt, and saves a local screenshot before dismissing it. Important actions include item names, quantities, and the verified Pyroxene balance increase when readable. Unreadable labels are identified as unreadable; an issued claim tap is not reported as a received reward. Unknown dialogs and capacity errors stop with diagnostics.

## Validation and current limits

On September 24, 2026, the staging account's two Pyroxene packs were already active with 23 days remaining. A single **2-Week AP Pack for USD 2.99** was purchased through ADB and Google Play. The game confirmed delivery. Claiming Product mail delivered **176 Pyroxenes**, independently verified by a before/after balance check; the store then showed 14 days of AP-pack coverage. No further purchase was needed for testing.

This initial transaction was executed through inspected ADB steps before the runner was written. Sanitized screenshots cover that checkout, its optional post-payment prompt, delivery, ownership before/after, mailbox claim, and reward receipt. Automated purchase decisions and failure recovery are exercised offline without charging money. Monthly and Half Monthly purchase confirmation flows have not been live-tested because those packs remain active. The runner rejects any layout it cannot recognize.

The complete dashboard job was then run with every purchase switch off: restart, inspect all three active packs, visit both empty mailbox tabs, and return to an unobstructed home screen. No additional payment was attempted. Browser checks verified the off-by-default controls and that the failed-jobs box is hidden for an empty list and visible for a simulated payment failure.
