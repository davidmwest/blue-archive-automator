# loot gathered

this is the fun part. the loot box keeps the actual haul since the last clear: game icons, exact item names, and quantities. identical items get grouped together, with the good stuff at the top. clear starts a fresh count; it doesn't delete the history or screenshots.

## reading a receipt

The local receipt reader runs before a reward screen is dismissed. It identifies card boundaries independently of the artwork, reads the received `xN` quantity, opens each new card's tooltip, and saves the icon and tooltip screenshot. Wrapped names are retained; uncertain words leave the item unidentified. An `Owned` value is inventory, never the amount received.

- **Sweeps:** read only Final Rewards Earned. Per-sweep rows are not counted again. The magnifying-glass control opens Full List when more reward types exist than fit in the summary.
- **Mail, Tasks, Cafe, crafting, free packs, Tactical Challenge, and Total Assault rewards:** inspect Reward Acquired cards. Horizontal lists are rewound and read through overlapping pages.
- **Lessons:** inspect the Lesson Reward row. Student portraits and their relationship ranks are not inventory drops.

Full List uses the same inspection path with vertical scrolling. Its heading can appear before the cards are readable, so entry waits without input for up to eight seconds. An empty nested list cannot bypass its exit and leave the caller waiting for the covered sweep receipt. Opening and unreadable frames are saved for diagnosis. Overlap is reconciled in order, so visiting the same card again does not add another drop, while separate identical cards are retained. Every input requires a current capture, the expected game foreground, and a recognized receipt or tooltip. Inspection has a bounded time and input budget; a missing tooltip or unreadable amount remains visible for review.

Inspection saves its observations before returning to the task, including partial results if inspection fails. A later navigation or resource-check failure therefore cannot hide an already recorded reward. Resource intent checkpoints still require their existing postconditions and cannot be replayed merely because loot was captured.

Sweep revalidation compares the heading, complete Final reward row, and Confirm button. It excludes the decorative label column, where a tooltip-dismiss click can leave a fading animation. Changed cards, quantities, or controls still stop input. A rejected scroll read saves the exact frame and parsed page kind/card count for diagnosis. Full List checks also exclude the neutral-area dismissal pulse. Equipment tier outlines may animate only when fresh OCR independently confirms the same tier and the remaining artwork, quantities, heading, and controls still match.

Task-completion banners can briefly cover the receipt heading. The reader waits without input for up to 12 seconds for that banner to disappear, then applies the same receipt checks. A banner that stays visible or a changed foreground still stops inspection. Tooltip identity uses the complete rectangular text panel, including the item name, Owned value, and description; its animated pointer is excluded.

If optional loot inspection fails after an AP, Bounty, or Scrimmage sweep, the reader may dismiss an unchanged tooltip that it opened itself, exit a recognized Full List, and return to the original confirmed sweep. It saves incomplete item details for review and requires the original reward row, sweep count, task, and controls to match. The task must then verify its normal AP, ticket, and attempt changes before clearing the pending spend. An unknown screen or unverified resource change still keeps the hold; no sweep is repeated to recover a receipt.

## the dashboard

The default order is premium currency and recruitment items, student materials, AP and tickets, shop currencies, leveling and skill materials, crafting and gifts, credits, other items, then equipment at the bottom. Within a category, recognized material tiers precede alphabetical order. These are display priorities, not prices or a claim that every item has the same value to every player.

Named gifts use a bundled [51-item English catalog](../ba_automator/gifts.py), reviewed against [SchaleDB's gift data](https://github.com/SchaleDB/SchaleDB/blob/70a2c4b8982ca860687898e61848847a60ffe3b8/data/en/items.json) and current item pages on September 24, 2026: 35 regular, 13 luxury, and 3 special gifts. Matching tolerates case, whitespace, and typographic quotes/hyphens, with an explicit alias for the older Cottontail Detective title. Gift names take precedence over broad material keywords, so Movie Ticket and the Hot Spring detective book stay under crafting + gifts. Future gifts need a reviewed catalog update; the daemon never downloads item lists or guesses names. Existing receipts are regrouped without changing their recorded names or quantities.

The main cards follow the game's vertical layout: item name, framed icon, then grouped quantity. Icons retain their original proportions and fit inside the card without zooming or clipping. The reader keeps the complete framed artwork; compact sweep cards may retain the game's quantity overlay instead of cutting away the bottom of the item. Where the full slanted frame can be recognized, pixels outside its outline become transparent so neighboring cards do not show through. Ambiguous outlines retain the original crop. This display mask does not change the source pixels used to verify receipt identity. When the same verified item appears in several receipts, the dashboard prefers the larger, more complete crop and keeps equal-quality choices stable. Cleared receipts may supply a better picture but never contribute to the current count.

Equipment and everything else start collapsed; opening them is preserved when the dashboard refreshes. Each receipt has an itemized breakdown, its saved image, and its original action log. Review counts distinguish unread item names or amounts, known items with unverified full-list coverage, and missing item details. Unidentified historical drops sit in an expandable section with the same evidence links; its entry count groups matching icons and is not a count of individual units. Unknown quantities remain unknown; they are never treated as zero or inferred from an icon.

Relationship level-ups have their own section in the same box, near student materials. Each observed increase keeps its new level, readable stat changes, time, and a link to the saved screen. Two level-ups for the same student remain two events; ranks are never added together or counted as inventory. A heart after a Café tap only confirms relationship experience, so it does not create a level-up card. Older actions without structured rank/stat observations remain in the action log.

The game may show a portrait without a student name. The dashboard labels the student as unidentified unless the task has verified who received the increase. Unread levels and stats remain explicitly unknown; an observed `ATK +20` does not imply a known total ATK. Clearing the box also clears these displayed relationship gains, while retaining their action history and screenshots. A repeated inspection of the same saved screen cannot duplicate an increase or bring it back after a clear.

Old receipts can gain names from an exact previously verified icon hash or readable saved labels. Static screenshots cannot prove that off-screen items were captured, so their incomplete status stays visible. This is local OCR and image matching; no AI or external item service runs in the daemon.

## storage and accounting

- Important actions remain append-only. A receipt and its later task-completion action count as one receipt.
- A `.loot.json` file next to the receipt stores its observed cards and completeness. Partial enrichment preserves previously verified totals.
- Icons live under `state_dir/loot-icons`, addressed by a SHA-256 identifier. The HTTP endpoint accepts only those identifiers; it never accepts a file path.
- Clearing records a history cursor. Late completion or enrichment of an already-cleared receipt cannot bring that old loot back.
- Mail balance changes can corroborate credits and Pyroxenes. Natural AP regeneration and all resource costs are excluded from loot.

## validation

Sanitized game screenshots cover ordinary and wrapped item tooltips, the currency tooltip without an Owned line, a nine-card sweep summary, an expanded twelve-card Full List, labeled reward cards, and the Lesson Reward row. Offline tests cover duplicate cards, page overlap, unknown names and amounts, failed inspection, stale frames, foreground changes, clear semantics, malformed metadata, and icon-path restrictions. Total Assault captures also cover animated headings, delayed tooltips, scroll rebound, and clipped cards at the viewport edges. These checks retain exact observed item names and quantities; they do not use fuzzy matching to fill missing loot.

On September 24, 2026, live checks identified all nine items in the supplied sweep receipt, all twelve items in an expanded Hard 13-2 receipt, and all ten items in a fresh Hard 13-1 sweep. The final run automatically opened Full List, read every tooltip, closed both reward panels, verified the AP and attempt decrements, and returned home. A Tactical Challenge claim separately verified the regular Reward Acquired layout and its 3,480-credit tooltip without spending a battle ticket.

Three authorized test sweeps used 60 AP; the normal 100 AP floor was unchanged. The first test exposed the summary's hidden overflow and remains marked incomplete. Historical receipts that were dismissed before detailed inspection can also remain incomplete; the dashboard deliberately keeps that limitation visible. Long scrolling reward lists and the other task integrations have screenshot and offline transition coverage, but were not all claimed again during these live checks.

On September 25, the long Total Assault points receipt was recovered and fully read: twelve item types across overlapping pages, including 3,000,000 credits, 200 Pyroxenes, and 40 Eligma. This exposed delayed tooltips, animated heading spacing, scroll rebound, and partially visible edge cards; each fix has captured regression evidence. The final inspection completed, verified the disabled claim and unchanged raid-ticket count, and returned home. It enriched the original receipt without counting the recovery as another reward. This was staged recovery, not a fresh uninterrupted claim by the finished daemon.

On September 26, saved receipts and live resource counters reconciled earlier Bounty and AP holds without repeating a sweep. A fresh Hard 11-2 sweep identified nine item types and verified its 20 AP cost. Hard 11-1 initially failed while opening and reading Full List; staged recovery identified ten types but retained an incomplete flag because end-of-list overlap was not proven. Recovery returned to the original receipt and verified the AP and attempt changes.

A subsequent uninterrupted Hard 10-3 sweep automatically opened and closed Full List, identified all ten item types, verified its 20 AP cost, and advanced the rotation to 10-2. Full List entry settling, animated tooltip pointers, and task-completion banners now have sanitized regression fixtures. The recovered 11-1 receipt remains marked incomplete.

A later September 26 Hard 8-3 hold was reconciled from its saved receipt
(647 → 627 AP) and the live attempt counter (three → two). Cafe earnings had
already changed the current AP balance, so comparing that balance alone would
have been incorrect. The completed sweep was not repeated. Its left-pointing
Bluetooth Necklace tooltip now has a sanitized regression fixture; tooltip
recognition uses the rectangular panel rather than including its arrow.
The reader also waits through the Cafe reward-card scaling animation after a
tooltip closes, without tapping the bare receipt to hurry it along. Existing
partial receipts retain their known items and incomplete status.

A subsequent live Hard 8-2 sweep completed on September 26: 740 → 720 AP,
saved loot inspection, verified post-sweep balance, and return home. The old
Hard 8-3 intent was reconciled against its saved receipt and the live remaining
attempt count before this retry; it was not spent again.
