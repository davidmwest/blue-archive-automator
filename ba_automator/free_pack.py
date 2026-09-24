"""Claim the observed Free Daily Pack. This runner has no billing capability."""

from dataclasses import dataclass
from .actions import record_action
from .crafting_vision import bright, cyan, has, yellow
from .home_badges import badges
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .shop_vision import classify_shop, text_in
from .vision import classify, decode_frame


@dataclass(frozen=True)
class FreePackScreen:
    kind: str
    target: tuple | None = None
    red_dot: bool = False


def classify_free_pack(frame, words, *, home=False):
    if frame.shape[:2] != (720, 1280):
        return FreePackScreen("unknown")
    if (
        has(words, "buy pyroxene", (450, 50, 830, 125))
        and has(words, "purchase this product", (440, 120, 850, 180))
        and bright(frame, (400, 75, 460, 100))
    ):
        # Read cost separately from the decorative FREE sticker on the card.
        if (
            text_in(words, (275, 185, 525, 255)) == "Free Daily Pack"
            and text_in(words, (705, 410, 940, 480)) == "Free"
            and has(words, "cost", (570, 410, 680, 480))
            and has(words, "confirm", (660, 550, 860, 630))
            and yellow(frame, (665, 565, 850, 620))
        ):
            return FreePackScreen("free_confirm", (760, 592))
        return FreePackScreen("unknown")
    shop = classify_shop(frame, words)
    if shop.kind in {"delivered", "receipt"}:
        return shop
    if (
        has(words, "buy pyroxene", (450, 85, 830, 145))
        and has(words, "special sale", (290, 150, 475, 210))
        and has(words, "pyroxenes", (520, 150, 760, 210))
        and has(words, "packs", (810, 150, 990, 210))
        and bright(frame, (420, 90, 500, 130))
    ):
        if text_in(words, (257, 215, 508, 276)) == "Free Daily Pack":
            available = has(words, "can purchase 1 time s a day", (265, 410, 505, 450))
            exhausted = has(words, "can purchase 0 time s a day", (265, 410, 505, 450))
            if (
                available
                and text_in(words, (290, 445, 480, 484)) == "Free"
                and has(words, "purchase", (290, 480, 485, 530))
                and cyan(frame, (310, 485, 460, 520))
            ):
                return FreePackScreen("free_available", (382, 501))
            if exhausted:
                return FreePackScreen("free_empty", (1009, 113))
            return FreePackScreen("unknown")
        return FreePackScreen("store", (900, 178))
    if home:
        return FreePackScreen("home", (1015, 35), "free_pack" in badges(frame))
    return FreePackScreen("unknown")


class FreePackVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        if billing:
            return FreePackScreen("unknown")
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = classify(words, self.startup.matches(frame)).state == "home"
        return classify_free_pack(frame, words, home=home)


class FreePackRunner(ShopRunner):
    task = "free_pack"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", FreePackVision(startup))
        super().__init__(config, device, startup, **kwargs)

    def confirm(self, frame):
        if frame.screen.kind != "free_confirm":
            self.fail("Free Daily Pack and Cost: Free must both be verified")
        self.journal.save_image("free-confirm.png", frame.capture.png)
        record_action(
            self.config,
            "free_pack_requested",
            "Claiming Free Daily Pack: verified Cost: Free",
            task=self.task,
        )
        self.tap(frame, frame.screen.target, "Confirm Free Daily Pack (Cost: Free)")
        # Never blindly repeat confirmation, and never enter Google Play.
        result = self.wait({"delivered", "receipt"})
        return self.complete(result)

    def complete(self, result):
        self.journal.save_image("delivery.png", result.capture.png)
        if result.screen.kind == "delivered":
            record_action(
                self.config,
                "free_pack_delivered",
                "Free Daily Pack sent to mail; collection follows",
                task=self.task,
                evidence=str(self.run_dir / "delivery.png"),
            )
        else:
            record_action(
                self.config,
                "free_pack_received",
                "Free Daily Pack reward received",
                task=self.task,
                items=list(result.screen.items),
                evidence=str(self.run_dir / "delivery.png"),
                items_complete=len(result.screen.items) == 2
                and {i["name"] for i in result.screen.items} == {"Credit Points", "AP"},
            )
        self.tap(result, result.screen.target, "Close free-package delivery notice")
        frame = self.wait("free_empty")
        self.tap(frame, frame.screen.target, "Return home from free package")
        self.home()
        return self.finish()

    def run(self):
        frame = self.wait("home")
        if not frame.screen.red_dot:
            self.sleep(1)
            frame = self.wait("home")
            if not frame.screen.red_dot:
                self.phase("No Buy Pyroxene notification; free package skipped")
                return self.finish()
        for _ in range(4):
            frame = self.wait({"home", "store", "free_available", "free_empty"})
            if frame.screen.kind in {"free_available", "free_empty"}:
                break
            self.tap(
                frame,
                frame.screen.target,
                "Open Buy Pyroxene" if frame.screen.kind == "home" else "Select Packs",
            )
            self.sleep(2)
        frame = self.wait({"free_available", "free_empty"})
        if frame.screen.kind == "free_empty":
            self.tap(
                frame,
                frame.screen.target,
                "Free Daily Pack already claimed; return home",
            )
            self.home()
            return self.finish()
        self.tap(frame, frame.screen.target, "Open verified Free Daily Pack")
        return self.confirm(self.wait("free_confirm"))


def run_free_pack(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = FreePackRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
