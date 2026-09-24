# Task rewards

if Tasks has a red dot, collect what's ready. this is the **Tasks** button on home: daily, weekly, achievement, and challenge rewards. it doesn't start missions or follow their Shortcut buttons.

queue **collect tasks** on the dashboard, or run:

```sh
.venv/bin/python -m ba_automator --config config/local.toml tasks
```

The standalone job runs restart → Tasks collection → home. Daily runs this check last, after its optional AP sweeps, so completed activities can contribute their rewards. Collected AP stays available for the existing Spend AP schedule; collecting Tasks does not itself spend AP.

## Collection

- Require a recognized, unobstructed English home screen at 1280×720 and the small red/orange badge beside Tasks. If the badge is absent on two observations, finish without opening Tasks or sending collection input.
- Open Tasks and select **All**, even if another tab was previously selected. Claim only the recognized enabled yellow **Claim All** or separate daily completion **Claim** control.
- Require a **Reward Acquired** receipt after each claim. Rewind its horizontal list and scan overlapping pages to the other end. Keep full visible names and quantities; cropped edge cards are read on the next page. Merge repeated cards once within that receipt, and stop if overlapping quantities disagree or pages lose their overlap.
- Save the receipt panels and a combined evidence image. Add verified received items to **important actions** and **loot gathered**. Claim intents do not count as loot. Receipt images remain available for labels the reader might miss; the loot view marks these receipts for image review rather than promising every card was read.
- Recheck the All tab after every receipt, because claiming rewards can unlock another task. Collect the daily completion bonus separately if it remains available. Return home and verify the notification has cleared.

The job shares the serial queue and per-instance device lock. It allows at most 12 reward claims, 600 seconds, and 200 inputs. An ignored claim or unrecognized receipt stops instead of blindly claiming again. It never uses Shortcut controls to complete unfinished tasks. It makes no runtime AI calls.

## Live validation

September 24, 2026, on the staging BlueStacks Air instance:

- The initial claim exposed a Tasks-specific receipt layout that the mailbox recognizer did not accept. The receipt was preserved and read before continuing; the claim was not repeated.
- A scrolling receipt verified **20,000 credits, 20 Pyroxenes, 300 AP, 20 Expert Permits, one Intact Firing Pin, one Keystone, three Normal Activity Reports, and four Beginner Tech Notes** (two Gehenna, one Millennium, one Highlander).
- A newly available second claim verified **100 Expert Permits and one Keystone**. The separate daily completion reward verified **20 more Pyroxenes**.
- The remaining collection sequence returned to clear home in 145 seconds. The Tasks badge was gone. This total covers receipt scanning and follow-up claims, not the earlier calibration claim.
- A subsequent full dashboard job ran restart → Tasks and passed. After startup, the no-badge Tasks check finished in 12 seconds with **zero inputs** and no extra loot entries. Desktop and mobile dashboard checks verified the queue button, daily ordering, item totals, and all three receipt image links.

Sanitized fixtures cover both badge states, Claim All, the daily bonus, disabled controls, and scrolling/small receipts. Offline tests cover tab selection, newly unlocked rewards, missing receipts, receipt overlap, and queue ordering.
