# loot gathered

this is the fun part. the loot box keeps the actual haul since the last clear: game icons, exact item names, and quantities. identical items get grouped together, with the good stuff at the top. clear starts a fresh count; it doesn't delete the history or screenshots.

## reading a receipt

The local receipt reader runs before a reward screen is dismissed. It identifies card boundaries independently of the artwork, reads the received `xN` quantity, opens each new card's tooltip, and saves the icon and tooltip screenshot. Wrapped names are retained; uncertain words leave the item unidentified. An `Owned` value is inventory, never the amount received.

- **Sweeps:** read only Final Rewards Earned. Per-sweep rows are not counted again. The magnifying-glass control opens Full List when more reward types exist than fit in the summary.
- **Mail, Tasks, Cafe, crafting, free packs, and Tactical Challenge:** inspect Reward Acquired cards. Horizontal lists are rewound and read through overlapping pages.
- **Lessons:** inspect the Lesson Reward row. Student portraits and their relationship ranks are not inventory drops.

Full List uses the same inspection path with vertical scrolling. Overlap is reconciled in order, so visiting the same card again does not add another drop, while separate identical cards are retained. Every input requires a current capture, the expected game foreground, and a recognized receipt or tooltip. Inspection has a bounded time and input budget; a missing tooltip or unreadable amount remains visible for review.

Inspection saves its observations before returning to the task, including partial results if inspection fails. A later navigation or resource-check failure therefore cannot hide an already recorded reward. Resource intent checkpoints still require their existing postconditions and cannot be replayed merely because loot was captured.

## the dashboard

The default order is premium currency and recruitment items, student materials, AP and tickets, shop currencies, leveling and skill materials, equipment, crafting and gifts, credits, then other items. Within a category, recognized material tiers precede alphabetical order. These are display priorities, not prices or a claim that every item has the same value to every player.

The main cards show grouped quantities and locally captured game icons. Each receipt has an itemized breakdown, its saved image, and its original action log. Unidentified historical drops sit in an expandable section with the same evidence links. Unknown quantities remain unknown; they are never treated as zero or inferred from an icon.

Old receipts can gain names from an exact previously verified icon hash or readable saved labels. Static screenshots cannot prove that off-screen items were captured, so their incomplete status stays visible. This is local OCR and image matching; no AI or external item service runs in the daemon.

## storage and accounting

- Important actions remain append-only. A receipt and its later task-completion action count as one receipt.
- A `.loot.json` file next to the receipt stores its observed cards and completeness. Partial enrichment preserves previously verified totals.
- Icons live under `state_dir/loot-icons`, addressed by a SHA-256 identifier. The HTTP endpoint accepts only those identifiers; it never accepts a file path.
- Clearing records a history cursor. Late completion or enrichment of an already-cleared receipt cannot bring that old loot back.
- Mail balance changes can corroborate credits and Pyroxenes. Natural AP regeneration and all resource costs are excluded from loot.

## validation

Sanitized game screenshots cover ordinary and wrapped item tooltips, the currency tooltip without an Owned line, a nine-card sweep summary, an expanded twelve-card Full List, labeled reward cards, and the Lesson Reward row. Offline tests cover duplicate cards, page overlap, unknown names and amounts, failed inspection, stale frames, foreground changes, clear semantics, malformed metadata, and icon-path restrictions.

On September 24, 2026, live checks identified all nine items in the supplied sweep receipt, all twelve items in an expanded Hard 13-2 receipt, and all ten items in a fresh Hard 13-1 sweep. The final run automatically opened Full List, read every tooltip, closed both reward panels, verified the AP and attempt decrements, and returned home. A Tactical Challenge claim separately verified the regular Reward Acquired layout and its 3,480-credit tooltip without spending a battle ticket.

Three authorized test sweeps used 60 AP; the normal 100 AP floor was unchanged. The first test exposed the summary's hidden overflow and remains marked incomplete. Historical receipts that were dismissed before detailed inspection can also remain incomplete; the dashboard deliberately keeps that limitation visible. Long scrolling reward lists and the other task integrations have screenshot and offline transition coverage, but were not all claimed again during these live checks.
