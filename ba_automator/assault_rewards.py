"""Claim rank and total-points rewards without exposing any battle inputs."""

from dataclasses import dataclass
import json

import cv2
import numpy as np

from .actions import record_action
from .assault_vision import classify_assault
from .crafting_vision import bright, yellow
from .locking import InstanceLock
from .loot_receipts import inspect_receipt
from .shop_runtime import ShopRunner
from .shop_vision import classify_shop, has
from .vision import classify, decode_frame


@dataclass(frozen=True)
class AssaultRewardScreen:
    kind: str
    target: tuple | None = None
    tab: str | None = None
    claimable: bool | None = None
    tickets: int | None = None
    items: tuple = ()


TABS = {"rank": (240, 240), "points": (240, 308)}


def classify_rewards(frame, words, *, home=False):
    if frame.shape[:2] != (720, 1280):
        return AssaultRewardScreen("unknown")
    receipt = classify_shop(frame, words)
    if receipt.kind == "receipt":
        return AssaultRewardScreen("receipt", receipt.target, items=receipt.items)
    modal = (has(words, "total assault info", (490, 75, 790, 135))
             and bright(frame, (320, 85, 450, 128))
             and has(words, "detailed rank info", (245, 140, 485, 195))
             and has(words, "reward details", (815, 140, 1040, 195)))
    if modal:
        if (has(words, "rank reward", (130, 208, 347, 270))
                and has(words, "total points rewards", (108, 276, 367, 338))
                and has(words, "reward claim period", (399, 540, 637, 585))
                and has(words, "claim", (990, 545, 1130, 608))):
            # The selected tab has dark-blue fill; the other has white fill.
            selected = []
            for name, (_, y) in TABS.items():
                b, g, r = np.median(frame[y-20:y+20, 102:117], axis=(0, 1))
                if 80 < b < 160 and 55 < g < 125 and 25 < r < 90 and b > r + 30:
                    selected.append(name)
            if len(selected) != 1:
                return AssaultRewardScreen("unknown")
            tab = selected[0]
            body = (has(words, "upon season s end", (465, 215, 690, 530)) if tab == "rank"
                    else has(words, "total season rank points", (395, 213, 681, 535)))
            if not body:
                return AssaultRewardScreen("unknown")
            active = yellow(frame, (949, 552, 1158, 605))
            hsv = cv2.cvtColor(frame[552:605, 949:1158], cv2.COLOR_BGR2HSV)
            disabled = float(((hsv[:, :, 1] < 35) & (hsv[:, :, 2] > 160)).mean()) > .75
            if not active and not disabled:
                return AssaultRewardScreen("unknown")
            return AssaultRewardScreen("rewards", (1056, 578) if active else None,
                                       tab=tab, claimable=active)
        if has(words, "my info", (180, 202, 300, 257)):
            return AssaultRewardScreen("rank_info", (915, 170))
        return AssaultRewardScreen("unknown")
    base = classify_assault(frame, words, home=home)
    if base.kind == "menu" and has(words, "rewards", (1090, 620, 1260, 685)):
        return AssaultRewardScreen("menu", (1180, 655), tickets=base.tickets)
    if base.kind in {"home", "campaign"}:
        return AssaultRewardScreen(base.kind, base.target)
    return AssaultRewardScreen("unknown")


class AssaultRewardVision:
    def __init__(self, startup):
        self.startup = startup

    def analyze(self, png, *, billing=False):
        frame = decode_frame(png)
        words = self.startup.read(frame)
        home = not billing and classify(words, self.startup.matches(frame)).state == "home"
        return classify_rewards(frame, words, home=home)


class AssaultRewardsRunner(ShopRunner):
    task = "assault_rewards"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", AssaultRewardVision(startup))
        super().__init__(config, device, startup, **kwargs)

    def budget(self):
        # Rank and points can each contain a long, paginated reward receipt.
        # Allow both bounded inspections plus navigation without widening the
        # shared limits for paid packs, mail, or other ShopRunner jobs.
        if self.clock() - self.started > 1920 or self.actions >= 400:
            self.fail(f"{self.task} reached its time or input limit")

    def important(self, action, detail, **extra):
        record_action(self.config, action, detail, task=self.task, **extra)
        self.phase(detail)

    def collect(self, menu):
        initial_tickets = menu.screen.tickets
        if initial_tickets is None:
            self.fail("Total Assault reward visit could not read the ticket balance")
        self.tap(menu, menu.screen.target, "Open Total Assault rewards")
        frame = self.wait({"rank_info", "rewards"})
        if frame.screen.kind == "rank_info":
            self.tap(frame, frame.screen.target, "Open Reward Details")
            frame = self.wait("rewards")
        claimed = []
        for tab in TABS:
            if frame.screen.tab != tab:
                self.tap(frame, TABS[tab], f"Inspect Total Assault {tab} rewards")
                frame = self.wait("rewards", predicate=lambda s: s.tab == tab)
            self.journal.save_image(f"{tab}-before.png", frame.capture.png)
            if frame.screen.claimable is False:
                self.phase(f"Total Assault {tab} rewards are not currently claimable")
                continue
            if frame.screen.claimable is not True or frame.screen.target is None:
                self.fail(f"Cannot verify the Total Assault {tab} Claim control")
            self.important("assault_reward_requested", f"Claiming Total Assault {tab} rewards", reward=tab)
            self.tap(frame, frame.screen.target, f"Claim Total Assault {tab} rewards")
            result = self.wait("receipt")
            evidence = self.run_dir / f"{tab}-receipt.png"
            self.journal.save_image(evidence.name, result.capture.png)
            result = inspect_receipt(self, result, evidence)
            sidecar = evidence.with_suffix(".loot.json")
            loot = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
            if loot.get("items_complete") is not True:
                self.fail(
                    "Total Assault reward receipt is incomplete; left open for inspection"
                )
            items = loot.get("items", list(result.screen.items))
            label = ", ".join(f'+{i["quantity"]:,} {i.get("name") or "unidentified item"}'
                              for i in items if i.get("quantity"))
            self.important("assault_rewards_received", f"Total Assault {tab} rewards: {label or 'receipt saved'}",
                           reward=tab, items=items, evidence=str(evidence),
                           items_complete=bool(loot.get("items_complete")))
            self.tap(result, result.screen.target, "Close Total Assault reward receipt")
            frame = self.wait("rewards", predicate=lambda s: s.tab == tab)
            if frame.screen.claimable is not False:
                self.fail("Total Assault reward is still claimable after receipt; no repeated claim sent")
            claimed.append(tab)
        self.tap(frame, (1166, 107), "Close Total Assault Info")
        final = self.wait("menu")
        if final.screen.tickets != initial_tickets:
            self.fail("Total Assault ticket balance changed during reward collection")
        self.journal.record("rewards_checked", claimed=claimed, tickets_before=initial_tickets,
                            tickets_after=final.screen.tickets)
        self.tap(final, (1237, 23), "Return home from Total Assault rewards")
        self.home()
        return self.finish()

    def run(self):
        self.navigate("home", "campaign", (1200, 641))
        menu = self.navigate("campaign", "menu", (906, 456))
        return self.collect(menu)


def run_assault_rewards(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = AssaultRewardsRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
