# Dashboard mascot artwork

The dashboard uses generated Maid Arisu fan art as small decorative accents.
These images depict a Blue Archive character; see [third-party notices](../THIRD_PARTY_NOTICES.md)
for the artwork's licensing boundary. They are not recognition templates and
are not used by the automation logic.

The following sibling assets were created on September 26, 2026 with the
built-in image generation tool. The existing `maid-arisu.png` was the character
and rendering reference for the first three poses; the heart pose used
`maid-arisu-checklist.png` to preserve that style. Each final PNG was visually reviewed and checked for
an actual alpha channel; the original mascot remains unchanged.

| File | Pose | Size |
| --- | --- | --- |
| `ba_automator/web/maid-arisu-checklist.png` | Checking off a clipboard | 1254 × 1254 |
| `ba_automator/web/maid-arisu-tea.png` | Quiet tea break | 1254 × 1254 |
| `ba_automator/web/maid-arisu-loot.png` | Sweeping up coins, inspired by the user's suggested burst-skill motif | 1254 × 1254 |
| `ba_automator/web/maid-arisu-heart.png` | Hugging a pink heart to celebrate relationship gains | 1254 × 1254 |

<img src="../ba_automator/web/maid-arisu-checklist.png" alt="Maid Arisu checking off her clipboard" width="200"> <img src="../ba_automator/web/maid-arisu-tea.png" alt="Maid Arisu taking a tea break" width="200"> <img src="../ba_automator/web/maid-arisu-loot.png" alt="Maid Arisu sweeping up sparkling gold coins" width="200">

<img src="../ba_automator/web/maid-arisu-heart.png" alt="Maid Arisu hugging a pink heart" width="200">

Use these as restrained accents, with empty alternative text when they are
purely decorative. Display at a small CSS size with `object-fit: contain` so
the halo, hair, and props remain visible.

Foreground mascots on the dashboard open their original full-size PNG in a
new tab. Background decorations remain unlinked.

## Generation prompts

### Checklist

```text
Use case: stylized-concept
Asset type: small decorative transparent PNG mascot for the Maid in Schale automation dashboard.
Primary request: draw new fan art of Arisu (Alice) from Blue Archive in her maid outfit, holding a small clipboard against her chest and happily checking off a task with a pencil. Use the supplied image as a character and rendering-style reference only; create a new pose.
Subject: one cute chibi Arisu with oversized bright blue eyes, long dark navy hair and blue highlights, side ponytail tied with blue ribbon, white ruffled maid headband, cyan pixel-shaped luminous halo above her head, black and white modest frilled maid dress with a blue bow, white apron, black shoes. Fully clothed cheerful helpful expression. Her clipboard has only three tiny check marks and ruled lines, no lettering.
Style: polished clean anime chibi illustration, soft cel shading, crisp navy outlines, cool blues and white, matching the reference mascot. Simple coherent silhouette that reads clearly at 160px.
Composition: full character centered, comfortably inside frame, head/halo/shoes all visible, approximately square artboard, little padding, no scene and no floor.
Background: genuinely transparent alpha around the character. No checkerboard drawn into the image, no opaque rectangle, no text, no logos or watermark. No extra characters or weapons.
```

The initial image had a painted checkerboard. A second built-in edit removed
only that background and retained the pose, clipboard, pencil, clothing,
framing, and colors as a transparent cutout.

### Tea

```text
Use case: stylized-concept
Asset type: small decorative transparent PNG mascot for the empty or paused queue in the Maid in Schale dashboard.
Primary request: new fan art of Arisu (Alice) from Blue Archive dressed as a maid, sitting quietly with her legs tucked beside her and holding a little blue teacup and saucer. She looks pleased and peaceful, ready for the next task. Use the supplied image as a character/rendering reference only, with a new pose.
Subject: one cute chibi Arisu, oversized bright blue eyes, long dark navy hair with blue highlights and side ponytail tied by blue ribbon, white ruffled headband, cyan pixel-shaped luminous halo, modest black-and-white frilled maid dress, white apron, blue bow. Fully clothed, skirt and apron neatly cover her seated pose.
Style: polished anime chibi art, crisp navy outlines, soft cel shading, cool blues and white matching the reference. Simple silhouette readable at 160 pixels.
Composition: full seated character, compact near-square composition, head halo and shoes fully inside frame, comfortable small margin, no table/furniture/room. Include only the character, teacup, and saucer.
Background: actual transparent alpha. No opaque rectangle, no drawn checkerboard, no text/logo/watermark, no additional characters or weapons.
```

### Coins

```text
Use case: stylized-concept
Asset type: small transparent fan-art decoration beside a loot collection header in Maid in Schale dashboard.
Primary request: draw a new cute chibi Maid Arisu (Alice) from Blue Archive enthusiastically sweeping up a little pile of sparkling gold coins with her maid broom, inspired by the coin-sweeping motif of her ultimate burst skill. Use the supplied picture as a reference for the character and art style only; create a new compact pose.
Subject: one chibi Arisu with long flowing dark navy hair with blue highlights, bright blue eyes, side ponytail tied by blue ribbon, white ruffled maid headband, cyan pixel-shaped luminous halo, modest black-and-white frilled maid dress with white apron and blue bow, white stockings and black shoes. Fully clothed. Both hands hold the broom diagonally as its bristles sweep five to seven simple gold coins into a neat small pile; a couple little star-shaped sparkles show the loot gathering. Cheerful pleased expression, dynamic but uncluttered silhouette, readable at 160px.
Style: polished anime chibi illustration, crisp navy outlines and soft cel shading matching the reference mascot; cool blue and white plus small warm gold coin accents.
Composition: full-body character and broom, halo and shoes and coin pile all entirely visible inside a near-square canvas with a little margin. No scene, room, floor, lettering, logos, or watermark.
Background: genuine transparent alpha around the subject, not a checkerboard pattern and not a solid-colored rectangle. Output a transparent PNG cutout.
```

The initial image had a black backdrop. The final built-in edit used:

```text
Use case: background-extraction
Edit target: supplied coin-sweeping Maid Arisu illustration.
Change ONLY the background: remove all the black backdrop and make it actually transparent alpha. This must be an RGBA PNG with alpha=0 in empty areas. The black background in the input is unwanted; do not preserve it. Keep the character's own dark navy hair, black maid dress, black shoes and dark broom parts intact. Preserve the exact character, cheerful face, pose, broom, gold coins, stars, cyan halo, framing and colors. Leave transparent cutouts between the hair strands and within the halo. Do not replace the backdrop with another solid color or a checkerboard pattern. Output clean transparent cutout artwork.
```

### Heart

```text
Use case: stylized-concept
Asset type: small decorative transparent PNG mascot for relationship gains in the Maid in Schale automation dashboard.
Primary request: new cute chibi fan art of Arisu (Alice) from Blue Archive in her maid outfit, cheerfully hugging a soft pink heart cushion with both arms. Use the supplied checklist picture as a character and rendering-style reference only; create a new compact pose. One or two tiny pink heart-shaped sparkles can float nearby.
Subject: one chibi Arisu with bright oversized blue eyes, long dark navy hair with blue highlights, side ponytail tied by blue ribbon, white ruffled maid headband, luminous cyan pixel-shaped halo, modest fully clothed black-and-white frilled maid dress, white apron and stockings, blue bow, black shoes. Warm happy expression. Both hands clearly wrap around the pink heart cushion at her chest.
Style: polished clean anime chibi illustration, soft cel shading and crisp navy outlines matching the supplied reference, cool blues and white with pink heart accents. Coherent silhouette readable at 160px.
Composition: full body centered comfortably inside a near-square artboard, halo, hair and shoes all inside frame, little padding, no scenery or floor. No clipboard or pencil.
Background: actual transparent alpha, RGBA PNG with alpha=0 in empty areas, including inside the halo and between hair strands. The input's black-looking background is transparency; preserve genuine transparency. No opaque rectangle, no checkerboard painted into image, no text, lettering, logos, watermark, extra characters or weapons.
```

The initial output painted a checkerboard instead of transparency. Two
built-in background-extraction edits preserved the pose and illustration;
the final output was verified as RGBA with genuine transparent pixels.

First edit:

```text
Use case: background-extraction
Edit target: supplied heart-hugging Maid Arisu illustration.
Change ONLY the background: remove all the painted gray checkerboard and make it actually transparent alpha. This must be an RGBA PNG with alpha=0 in empty areas. The checkerboard pattern is unwanted; do not preserve it or draw a new checkerboard. Keep the character's own dark navy hair, black maid dress, black shoes, white apron, pink heart cushion, two floating little pink hearts, and cyan pixel halo intact. Preserve the exact character, face, pose, framing and colors. Leave transparent cutouts between all hair strands and within the halo. Do not replace backdrop with a solid color. Output clean genuine transparent cutout artwork.
```

Final edit:

```text
Remove the background. Return this identical character illustration as a transparent PNG sticker, with actual alpha transparency, no visible checkerboard. Keep Maid Arisu, pink hearts, halo, and all clothing unchanged.
```
