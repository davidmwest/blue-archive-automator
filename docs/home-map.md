# Home and Campaign map

Coordinates refer to the global English 1280×720, 320 DPI profile. The map is stored in [`ba_automator/assets/home_map.json`](../ba_automator/assets/home_map.json) and displayed by the local dashboard. It is a reviewed navigation reference, not a generic click script.

All listed home controls were visually checked against a clear staging screenshot during the September 23–24, 2026 session. Cafe, Lesson, and Campaign were opened and their destinations verified. Lesson navigation was exercised during the Lessons implementation; this verifies the route independently of full task completion. `visual_verified` means the control and position were seen; `route_verified` means its destination was exercised. The existing `verified` flag follows route verification for the dashboard badge.

| Home control | Center | Navigation status |
| --- | --- | --- |
| Cafe | `(97,691)` | Label target; destination verified |
| Lesson | `(210,659)` | Destination verified |
| Students | `(323,659)` | Position verified |
| Formation | `(436,659)` | Position verified |
| Social | `(548,659)` | Position verified |
| Crafting | `(658,689)` | Label target; destination verified |
| Shop | `(769,659)` | Position verified |
| Recruit | `(877,659)` | Position verified |
| Event Recap | `(1063,617)` | Position verified; permanent archive |
| Campaign | `(1200,641)` | Destination verified |
| Tasks | `(50,234)` | Position verified; achievement rewards |
| MomoTalk | `(135,142)` | Position verified |
| Notice | `(51,143)` | Position verified |
| Mail | `(1155,36)` | Position verified |
| Menu | `(1226,36)` | Position verified |
| Rotating event banner | `(1193,207)` | Position verified; card identity changes |

After a September 25 startup, Home looked ready but Campaign did not respond to either a normal tap or a 150 ms press. The same normal tap succeeded about 28 seconds later; the underlying cause is unconfirmed. Home → Campaign now checks the destination after each attempt and, if Home is still recognized, waits ten seconds before its next freshly verified attempt. It keeps the three-tap limit and returns immediately when Campaign opens. A queued Spend AP run subsequently opened Campaign on a delayed retry and reached Hard missions. September 26 live checks also verified the Cafe and Crafting text-label targets after saved failures showed ignored taps on their illustrations. Each runner recognizes the label again before sending input.

Campaign contains Mission, Story, Bounty, Commissions, Scrimmage, Total Assault, Joint Firing Drill, Grand Assault, Tactical Challenge, and Final Restriction Release in the current screenshot. Mission is near `(821,232)`; the Spend AP job has verified this route into Hard missions. Home's Tasks control is separate from playable missions.

The **Campaign upper-left event entry near `(100,162)`** was exercised: Lore Pursuit opened a spoiler notice, its contextual Confirm advanced to a play guide, and the recognized guide X led to the event page with Lore Pursuit and Quest/Challenge controls. That verifies this route for the observed event and session. It does not validate every card that can occupy the same position or the separate home-carousel route.

A future event job must recognize the configured event before selecting it and verify the destination afterward. See [event navigation research](event-navigation-research.md) and [event profiles](event-profiles.md). Event Recap, Main Story, a recruitment banner, a reward-only event shop, and the current playable event are separate destinations.
