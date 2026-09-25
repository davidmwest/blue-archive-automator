"""Collect Tactical Challenge rewards without issuing any battle inputs."""

from dataclasses import dataclass
import re
from .actions import record_action
from .ap_vision import classify_ap
from .crafting_vision import has, bright, yellow, within
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .shop_vision import classify_shop, text_in
from .vision import classify, decode_frame


@dataclass(frozen=True)
class TacticalScreen:
    kind: str
    target: tuple | None = None
    reward: str | None = None
    items: tuple = ()
    claims: tuple = ()
    tickets: int | None = None


def classify_tactical(frame, words, *, home=False):
    if frame.shape[:2] != (720, 1280):
        return TacticalScreen("unknown")
    receipt = classify_shop(frame, words)
    if receipt.kind == "receipt":
        return TacticalScreen("receipt", receipt.target, items=receipt.items)
    tickets = re.fullmatch(
        r"Tickets Owned (\d+)/(\d+)", text_in(words, (45, 470, 290, 512))
    )
    if (
        any(
            re.fullmatch(r"tactical challenge(?: [0-9])?", w.normalized)
            for w in within(words, (95, 0, 395, 50))
        )
        and any(
            re.fullmatch(r"season(?: [0-9])?", w.normalized)
            for w in within(words, (435, 120, 590, 180))
        )
        and bright(frame, (390, 3, 435, 29))
        and any(
            w.normalized.startswith("time reward")
            for w in within(words, (45, 348, 289, 395))
        )
        and any(
            w.normalized.startswith("daily reward")
            for w in within(words, (45, 430, 289, 472))
        )
        and tickets
    ):
        claims = []
        for name, y in (("time", 388), ("daily", 467)):
            box = (310, y - 25, 400, y + 25)
            if not has(words, "claim", box):
                return TacticalScreen("unknown")
            if yellow(frame, box):
                claims.append((name, (353, y)))
        return TacticalScreen("tactical", claims=tuple(claims), tickets=int(tickets[1]))
    if home:
        return TacticalScreen("home", (1200, 641))
    if classify_ap(frame, words).kind == "campaign" and has(
        words, "tactical challenge", (795, 575, 940, 640)
    ):
        return TacticalScreen("campaign", (868, 581))
    return TacticalScreen("unknown")


class TacticalVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = (
            not billing and classify(words, self.startup.matches(frame)).state == "home"
        )
        return classify_tactical(frame, words, home=home)


class TacticalRewardsRunner(ShopRunner):
    task = "tactical_rewards"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", TacticalVision(startup))
        super().__init__(config, device, startup, **kwargs)

    def collect_menu(self, frame):
        initial_tickets = frame.screen.tickets
        visited = set()
        # Exactly one claim per reward type per visit. Time credits start
        # accumulating again immediately, so zero must not be polled forever.
        for _ in range(2):
            choice = next(
                (
                    (name, target)
                    for name, target in frame.screen.claims
                    if name not in visited
                ),
                None,
            )
            if choice is None:
                break
            name, target = choice
            self.journal.save_image(f"{name}-before.png", frame.capture.png)
            record_action(
                self.config,
                "tactical_reward_requested",
                f"Collecting Tactical Challenge {name} reward",
                task=self.task,
            )
            self.tap(frame, target, f"Claim Tactical Challenge {name} reward")
            result = self.wait("receipt")
            evidence = f"{name}-receipt.png"
            self.journal.save_image(evidence, result.capture.png)
            items = list(result.screen.items)
            label = ", ".join(f'+{i["quantity"]:,} {i["name"]}' for i in items)
            record_action(
                self.config,
                "tactical_rewards_received",
                f'Tactical Challenge {name} reward: {label or "receipt saved; item labels unreadable"}',
                task=self.task,
                reward=name,
                items=items,
                evidence=str(self.run_dir / evidence),
                items_complete=(
                    name == "time"
                    and len(items) == 1
                    and items[0]["name"] == "Credit Points"
                ),
            )
            self.phase(f'Collected {name} reward: {label or "receipt saved"}')
            self.tap(
                result, result.screen.target, "Close Tactical Challenge reward receipt"
            )
            visited.add(name)
            frame = self.wait("tactical")
            if frame.screen.tickets != initial_tickets:
                self.fail(
                    "Tactical Challenge ticket count changed during reward collection"
                )
            if name == "daily" and any(
                kind == "daily" for kind, _ in frame.screen.claims
            ):
                self.fail(
                    "Daily reward is still claimable after its receipt; no repeat claim sent"
                )
        self.journal.save_image("rewards-checked.png", frame.capture.png)
        self.journal.record(
            "rewards_checked",
            claimed=sorted(visited),
            tickets_before=initial_tickets,
            tickets_after=frame.screen.tickets,
        )
        self.tap(frame, (1237, 23), "Return home from Tactical Challenge")
        self.home()
        if not visited:
            self.phase("Tactical Challenge rewards are not currently claimable")
        return self.finish()

    def run(self):
        self.navigate("home", "campaign", (1200, 641))
        frame = self.navigate("campaign", "tactical", (868, 581))
        return self.collect_menu(frame)


def run_tactical_rewards(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = TacticalRewardsRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
