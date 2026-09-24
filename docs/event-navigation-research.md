# Active event navigation research

Research and live inspection: September 23–24, 2026. The Campaign route below was verified on the staging instance; a universal static active-event selector was not established.

## Finding

The alternate route **Home → Campaign → the event icon at the upper left** was verified live. The Lore Pursuit card near `(100,162)` opened a spoiler notice; its contextual Confirm advanced to a play guide, and the recognized guide X led to **Lore Pursuit with Quest/Challenge controls**. This verifies the observed card and destination, not a static selector for every active event. An existing automator explicitly treats the Campaign entry as a rotating banner too.

The home event banner is near `(1193,207)`. Its location was visually verified, but that separate entrance was not used to establish the verified Campaign route. Campaign itself is entered from the fixed home control near `(1200,641)`.

The fixed **Event Recap** button beside Campaign is a different destination: permanent archived event stories and guide tasks. Do not use it as the active-event shortcut.

## Evidence

| Source | What it establishes | Limit |
| --- | --- | --- |
| [Nexon: 3/22/2022 patch notes](https://forum.nexon.com/bluearchive-en/board_view?allBoard=1&board=3217&thread=2110249) | Hina's Summer Vacation could be entered through Campaign's upper-left event icon. | Historical UI; the same general route is now confirmed by our live inspection. |
| [Nexon: 9/6/2022 patch notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=2121384) | Explicitly lists both the lobby banner and Campaign upper-left event icon. Also distinguishes playable event dates from the later shop/reward period. | Does not promise a nonrotating icon. |
| [BAAH: InEvent](https://github.com/BlueArchiveArisHelper/BAAH/blob/main/modules/AllTask/InEvent/InEvent.py) | Opens Campaign/Fight Center, then tries its upper-left entry near `(105,162)`. The function describes a scrolling banner and varies its waiting time between attempts. It checks for a real event page and available Quest stages afterward. | Code evidence for an alternate carousel, not proof of current layout. Do not copy its repeated-click strategy. |
| [BAAS: activity navigation](https://github.com/pur1fying/blue_archive_auto_script/blob/master/module/activities/activity_utils.py) | Recognizes event-specific entrance images before tapping near `(1196,195)` or `(100,149)`, then verifies the event screen. | Our live Campaign entry is near `(100,162)`; event-specific identity still needs recognition. |
| [BAAuto README](https://github.com/RedDeadDepresso/BAAuto) | Requires a cropped image of the desired event banner for event automation. | Supports identity-based targeting; supplies no universal static shortcut. |
| [Nexon: Event Recap notice](https://forum.nexon.com/bluearchive-en/board_view?thread=2290448) and [7/23/2024 patch notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=2607184) | Event Recap contains permanent Event Stories/Guide Tasks; immortalized stories have restricted content. | An archive entry is not evidence the live farming event is available. |
| [Nexon: Grand Assault guide](https://forum.nexon.com/bluearchive-en/board_view?board=3222&thread=2523199) and [Final Restriction Release guide](https://forum.nexon.com/bluearchive-en/board_view?allBoard=1&board=3222&thread=2727380) | Campaign also contains distinct combat modes, each with its own availability rules. | Campaign is a navigation hub, not an active-event list. |
| [Community firsthand UI discussion, May 2022](https://www.reddit.com/r/BlueArchive/comments/uk8b0a) | Players report both cycling lobby banners and a small Campaign event entry that can show an old event. | Old anecdotal confirmation, not current verification. |

The [current community event thread](https://www.reddit.com/r/BlueArchive/comments/1wgqui0/event_thread_main_story_act_2_ex_lore_pursuit/) identifies **Special Mission: Lore Pursuit**, playable September 15–29, 2026, with shop/reward access through October 6 (UTC). It links [Nexon's 9/15 patch notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=3541757); the official page's body was not available to the research text extractor. Live inspection confirmed the Lore Pursuit title, an entry countdown, and Quest/Challenge controls. The profile still separates the playable cutoff from the later reward period.

A similarly named Main Story chapter, an archived Event Recap story, a web event, a recruitment banner, and the playable timed event are separate destinations. A visible banner or an accessible shop can outlast the event's playable stages.

## Recommendation for our navigator

1. Prefer the verified Campaign route for the configured Lore Pursuit profile, while matching the current card identity before any tap. Its fixed location alone is insufficient.
2. If the desired card is absent, observe a bounded carousel cycle. The home carousel is an alternate entrance to validate; short-swipe enumeration is an option only after live swipe behavior is established.
3. Detect a settled banner, confirm its identity on a fresh frame, then tap once. The position is fixed; the item occupying that position is not.
4. Verify the destination's event identity and appropriate menu anchors before calling navigation successful. For farming, separately verify Quest availability and the playable period.
5. Return explicit results for `not_available`, `locked`, `rewards_only`, and `unknown`. A lock or countdown should stop selection attempts until conditions change.

No verified carousel pause control or universal static active-event button was found in the reviewed official posts and primary automator sources. Pause-on-touch/hold and swipe behavior remain live-test hypotheses. Set a time/card-count limit and preserve the observed banner sequence when no target is found.

## Implementation status

[`config/events/lore-pursuit.json`](../config/events/lore-pursuit.json) now stores the researched entrances, availability, guarded optional notices, and destination checks. `events.py` validates the profile and performs deterministic read-only matching with local OCR or supplied local template hits. There is no event navigation or farming job yet. The profile is researched ahead of time; runtime recognition makes no AI calls. See [event profiles](event-profiles.md).
