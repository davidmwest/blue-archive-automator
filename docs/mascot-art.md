# Dashboard mascot artwork

The dashboard uses generated Maid Arisu fan art as small decorative accents.
These images depict a Blue Archive character; see [third-party notices](../THIRD_PARTY_NOTICES.md)
for the artwork's licensing boundary. They are not recognition templates and
are not used by the automation logic.

The following sibling assets were created on September 26, 2026. The existing
`maid-arisu.png` was the character and rendering reference for the first three poses; the heart pose used
`maid-arisu-checklist.png` to preserve that style. Each final PNG was visually reviewed and checked for
an actual alpha channel; the original mascot remains unchanged.

| File | Pose | Size |
| --- | --- | --- |
| `ba_automator/web/maid-arisu-checklist.png` | Checking off a clipboard | 1254 × 1254 |
| `ba_automator/web/maid-arisu-tea.png` | Quiet tea break | 1254 × 1254 |
| `ba_automator/web/maid-arisu-loot.png` | Sweeping up coins, inspired by her burst-skill motif | 1254 × 1254 |
| `ba_automator/web/maid-arisu-heart.png` | Hugging a pink heart to celebrate relationship gains | 1254 × 1254 |

<img src="../ba_automator/web/maid-arisu-checklist.png" alt="Maid Arisu checking off her clipboard" width="200"> <img src="../ba_automator/web/maid-arisu-tea.png" alt="Maid Arisu taking a tea break" width="200"> <img src="../ba_automator/web/maid-arisu-loot.png" alt="Maid Arisu sweeping up sparkling gold coins" width="200">

<img src="../ba_automator/web/maid-arisu-heart.png" alt="Maid Arisu hugging a pink heart" width="200">

Use these as restrained accents, with empty alternative text when they are
purely decorative. Display at a small CSS size with `object-fit: contain` so
the halo, hair, and props remain visible.

Foreground mascots on the dashboard open their original full-size PNG in a
new tab. Background decorations remain unlinked.
