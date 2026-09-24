# Event profiles

Event behavior is researched ahead of time and saved as local JSON. Runtime recognition uses configured text and local image matches. It makes no AI calls.

The version-1 loader and read-only recognition helpers are implemented. **There is no event navigation job, event farming task, or event CLI command yet.** A matching profile returns evidence and a candidate target; it does not send game input.

## Current profile

[`config/events/lore-pursuit.json`](../config/events/lore-pursuit.json) describes Special Mission: Lore Pursuit on global English at 1280×720. During live inspection, Campaign's upper-left entry near `(100,162)` opened a spoiler notice, then a play guide, then the Lore Pursuit event page with Quest/Challenge controls. The home entry is near `(1193,207)`.

Both entry regions can contain rotating content. The navigator must match the current event identity before selecting the configured point and verify the destination afterward. Event Recap is a separate permanent archive.

Availability records the playable cutoff of September 29, 2026 at 01:59 UTC and the reward/shop cutoff of October 6 at 01:59 UTC from the [current event thread](https://www.reddit.com/r/BlueArchive/comments/1wgqui0/event_thread_main_story_act_2_ex_lore_pursuit/), which links [Nexon's patch notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=3541757). The exact maintenance-end start time is omitted. The live screen still determines whether stages are unlocked and playable.

## Schema version 1

| Field | Meaning |
| --- | --- |
| `schema_version` | Exactly `1` |
| `id`, `title` | Stable lowercase event identifier and display title |
| `server` | `global-en` for the current implementation |
| `availability` | Optional `starts_at`, `playable_until`, and `rewards_until`, with timezone-aware ISO 8601 timestamps |
| `entries` | Named home/Campaign entrances with a fixed search `region`, an in-region `tap`, required `ocr_keywords`, and optional `template_refs` |
| `route_checks` | Guarded optional notices and exactly one destination check; each has a `region` and nonempty `required_ocr` |
| `sources` | Optional HTTPS research links |

Coordinates use `[x1, y1, x2, y2]` regions with exclusive right/bottom edges. Tap points use `[x, y]`. All must fit 1280×720. OCR phrases ignore case and punctuation, require complete words, and must occur inside the configured region with confidence at least 0.65. All entry keywords and all `required_ocr` phrases must match. When a route check supplies `any_ocr`, at least one of those phrases must also match.

Optional-notice `dismiss_control` values are limited to `confirm` and `recognized_close`. These describe the expected control; they do not approve an arbitrary Confirm/X button or provide an executable click instruction. A future runner must recognize the guarded notice and independently validate the control on a fresh frame.

Template references must point to existing local PNG files relative to the profile, without parent traversal. Only reviewed local crops should be added. No banner images are bundled in the initial profile. The recognition helper can accept hits from a local image matcher, keyed by the profile's template paths; the hit must lie inside the entry region.

The loader rejects unknown keys, unsupported servers, duplicate IDs, missing destination checks, invalid coordinates, unguarded steps, and contradictory dates.

## Python interfaces

```python
from datetime import datetime, timezone
from ba_automator.events import load_event_profile, inspect_entries, match_route_check
from ba_automator.vision import StartupVision

profile = load_event_profile("config/events/lore-pursuit.json")
vision = StartupVision()
matches = inspect_entries(
    profile,
    saved_png_bytes,
    vision,
    screen="campaign",  # caller has already recognized this screen
    at=datetime.now(timezone.utc),
)
```

`match_entries(profile, words, screen=..., at=..., template_hits=...)` performs the same decision on existing OCR words. It returns a tuple of `EntryMatch` records, without clicking. An empty result means no supported candidate. It excludes locked/upcoming/ended banner text and profiles outside their playable period. `profile.phase(at)` distinguishes `not_started`, `playable`, `rewards_only`, `closed`, and `unspecified`.

`match_route_check(profile, check_id, words)` checks contextual text for an optional notice or destination. It is a recognition result, not an action authorization. `inspect_entries` uses `StartupVision.read` and screenshot dimension validation; it performs no ADB calls and does not apply template matching itself.

Before an event runner is added, capture current entrance/destination fixtures, measure carousel behavior, and define freshness/retry limits. Keep selection, notices, destination verification, and actual farming as separate bounded steps. An event profile should be updated from reviewed evidence when the next event arrives; no runtime model is needed to interpret it.
