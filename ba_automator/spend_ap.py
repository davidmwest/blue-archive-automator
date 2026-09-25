"""Survey sweepable stages and spend existing AP without crossing its floor."""

from datetime import timedelta
from .actions import record_action
from .loot_receipts import inspect_receipt
from .ap_policy import choose_hard, default_order, sweep_count
from .ap_state import read_state, write_state
from .ap_vision import APVision
from .locking import InstanceLock
from .shop_runtime import ShopRunner


class APRunner(ShopRunner):
    task = "spend_ap"

    def __init__(
        self, config, device, startup, *, vision=None, allow_retry=False, **kwargs
    ):
        super().__init__(
            config, device, startup, vision=vision or APVision(startup), **kwargs
        )
        self.allow_retry = allow_retry
        self.state = None

    def budget(self):
        if self.clock() - self.started > 1800 or self.actions >= 1200:
            self.fail("AP task reached its time or input limit")

    def save(self):
        write_state(self.config, self.state)

    def important(self, action, detail, **fields):
        record_action(self.config, action, detail, task=self.task, **fields)
        self.phase(detail)

    def swipe(self, frame, *, down):
        self.budget()
        if (
            frame.screen.kind != "commission_list"
            or not frame.capture.is_fresh(self.clock())
            or self.device.foreground_package() != self.config.package
        ):
            self.fail("No fresh commission list for scrolling")
        # Short, slow drags leave overlapping rows despite list momentum.
        points = ((945, 295), (945, 535)) if down else ((945, 535), (945, 295))
        self.journal.record("intent", operation="swipe", points=points)
        if not self.device.swipe(
            *points,
            duration_ms=650,
            deadline=frame.capture.deadline,
            monotonic=self.clock,
        ):
            self.fail("Commission scroll expired")
        self.actions += 1
        self.sleep(0.9)

    def go_home(self, frame):
        if frame.screen.kind == "detail":
            self.tap(
                frame,
                (1127, 109 if frame.screen.strategy == "elephs" else 139),
                "Close mission info",
            )
            frame = self.wait({"hard_list", "commission_list"})
        if frame.screen.kind != "home":
            if frame.screen.kind not in {
                "hard_list",
                "normal",
                "commission_list",
                "commissions",
                "campaign",
            }:
                self.fail("Cannot return home from an unrecognized AP screen")
            self.tap(frame, (1237, 24), "Return home")
        self.home()

    def enter_commission(self, strategy):
        self.navigate("home", "campaign", (1200, 641))
        self.navigate("campaign", "commissions", (711, 505))
        frame = self.wait("commissions")
        self.tap(
            frame,
            (1013, 205 if strategy == "reports" else 310),
            f"Open {strategy} commissions",
        )
        return self.wait("commission_list", predicate=lambda s: s.strategy == strategy)

    def commission_top(self, frame):
        for _ in range(12):
            if any(s.id == "A" for s in frame.screen.stages):
                return frame
            self.swipe(frame, down=True)
            frame = self.wait("commission_list")
        self.fail("Commission list did not reach its first stage")

    def survey_commission(self, strategy):
        self.phase(f"Scanning three-star {strategy} commissions")
        frame = self.commission_top(self.enter_commission(strategy))
        seen = {}
        last = None
        stationary = 0
        for _ in range(24):
            signature = tuple(
                (s.id, s.stars, s.target is not None) for s in frame.screen.stages
            )
            for s in frame.screen.stages:
                if s.id in seen and seen[s.id].stars != s.stars:
                    self.fail("Commission star readings changed during survey")
                seen[s.id] = s
            stationary = stationary + 1 if signature == last else 0
            if stationary >= 2:
                break
            last = signature
            self.swipe(frame, down=False)
            frame = self.wait(
                "commission_list", predicate=lambda s: s.strategy == strategy
            )
        else:
            self.fail("Commission list did not reach a stable end")
        ids = sorted(seen)
        if ids != [chr(n) for n in range(65, ord(ids[-1]) + 1)]:
            self.fail(
                "Commission survey missed a stage; cannot select the highest clear"
            )
        eligible = [
            s.id for s in seen.values() if s.stars == 3 and s.target is not None
        ]
        best = max(eligible) if eligible else None
        previous = self.state["commissions"].get(strategy)
        if previous and (best is None or best < previous):
            self.fail(
                "Commission survey fell below a previously verified clear; no AP spent"
            )
        self.go_home(frame)
        return best

    def enter_hard(self):
        self.navigate("home", "campaign", (1200, 641))
        # Mission remembers its last mode and area.
        for _ in range(3):
            frame = self.wait({"campaign", "normal", "hard_list"})
            if frame.screen.kind != "campaign":
                break
            self.tap(frame, (843, 230), "Open missions")
            self.sleep(2)
        frame = self.wait({"normal", "hard_list"})
        if frame.screen.kind == "normal":
            self.tap(frame, (1067, 160), "Select Hard missions")
            frame = self.wait("hard_list")
        return frame

    def area_step(self, frame, right):
        area = frame.screen.area
        if frame.screen.kind != "hard_list" or not isinstance(area, int):
            self.fail("No recognized Hard area for navigation")
        expected = area + (1 if right else -1)
        if not 1 <= expected <= 99:
            self.fail("Requested mission area is not reachable")
        for attempt in range(3):
            if frame.screen.kind != "hard_list" or frame.screen.area != area:
                self.fail("Hard mission area changed before navigation")
            if frame.screen.right if right else frame.screen.left:
                break
            if attempt == 2:
                self.fail("Requested mission area is not reachable")
            # The previous tap's blue pulse can briefly hide the arrow's normal
            # color. Observe the same area again rather than treating it as an edge.
            if attempt == 0:
                self.phase("Waiting for the Hard mission arrow to become visible")
            self.sleep(0.7)
            frame = self.capture()
        self.tap(frame, (1240 if right else 42, 358), f"Open Hard area {expected}")
        return self.wait("hard_list", predicate=lambda s: s.area == expected)

    def survey_hard(self):
        frame = self.enter_hard()
        for _ in range(99):
            if not frame.screen.right:
                break
            frame = self.area_step(frame, True)
        else:
            self.fail("Could not locate the highest available mission area")
        highest = frame.screen.area
        eligible = []
        areas = []
        while True:
            self.phase(f"Scanning Hard area {frame.screen.area} of {highest}")
            # Same area, same three stage star readings on a second frame.
            confirmed = self.wait(
                "hard_list", predicate=lambda s: s.area == frame.screen.area
            )
            if sorted((s.id, s.stars) for s in confirmed.screen.stages) != sorted(
                (s.id, s.stars) for s in frame.screen.stages
            ):
                self.fail("Hard mission stars changed during survey")
            frame = confirmed
            areas.append(frame.screen.area)
            eligible.extend(
                s.id
                for s in frame.screen.stages
                if s.stars == 3 and s.target is not None
            )
            self.journal.save_image(
                f"hard-area-{frame.screen.area}.png", frame.capture.png
            )
            if frame.screen.area == 1:
                break
            frame = self.area_step(frame, False)
        if areas != list(range(highest, 0, -1)):
            self.fail("Hard survey missed an area")
        self.go_home(frame)
        return list(default_order(eligible))

    def survey(self):
        commissions = {
            key: self.survey_commission(key) for key in ("reports", "credits")
        }
        hard = self.survey_hard()
        self.state.update(
            commissions=commissions,
            hard_stages=hard,
            surveyed_at=self.wall_clock().isoformat(),
        )
        self.save()
        self.important(
            "ap_stages_scanned",
            f'Found {len(hard)} three-star Hard stages. Best commissions: reports {commissions["reports"] or "none"}, credits {commissions["credits"] or "none"}.',
        )

    def commission_detail(self, strategy, stage):
        frame = self.commission_top(self.enter_commission(strategy))
        for _ in range(18):
            target = next((s for s in frame.screen.stages if s.id == stage), None)
            if target:
                if target.stars != 3 or target.target is None:
                    self.fail(
                        "Selected commission is no longer a verified three-star clear"
                    )
                self.tap(frame, target.target, f"Inspect {strategy} {stage}")
                return self.wait(
                    "detail",
                    predicate=lambda s: s.strategy == strategy and s.stage == stage,
                )
            self.swipe(frame, down=False)
            frame = self.wait("commission_list")
        self.fail("Selected commission could not be located")

    def hard_area(self, frame, stage):
        area = int(stage.split("-")[0])
        while frame.screen.area != area:
            frame = self.area_step(frame, frame.screen.area < area)
        return frame

    def selected_quantity(self, frame, wanted):
        """Quantity controls do not spend; validate every observed change."""
        screen = frame.screen
        offset = 32 if screen.strategy == "elephs" else 0
        identity = (screen.strategy, screen.stage)
        if screen.count != wanted:
            maximum = min(
                screen.ap // screen.cost,
                screen.remaining if screen.remaining is not None else 999,
            )
            use_max = maximum - wanted < wanted - 1
            self.tap(
                frame,
                (1081 if use_max else 790, 300 + offset),
                "Set sweep quantity endpoint",
            )
            endpoint = maximum if use_max else 1
            frame = self.wait(
                "detail",
                predicate=lambda s: (s.strategy, s.stage) == identity
                and s.count == endpoint,
            )
            while frame.screen.count != wanted:
                step = 1 if frame.screen.count < wanted else -1
                expected = frame.screen.count + step
                self.tap(
                    frame,
                    (1015 if step == 1 else 855, 300 + offset),
                    "Adjust sweep count",
                )
                frame = self.wait(
                    "detail",
                    predicate=lambda s: (s.strategy, s.stage) == identity
                    and s.count == expected,
                )
        return frame

    def sweep(self, frame, count, *, next_stage=None):
        original = frame.screen
        if (
            original.ap is None
            or original.stars != 3
            or original.cost is None
            or original.target is None
            or original.strategy == "elephs"
            and (original.remaining is None or not 0 < original.remaining <= 3)
            or count < 1
            or count
            > sweep_count(
                original.ap,
                self.config.ap_floor,
                original.cost,
                original.remaining if original.remaining is not None else 999,
            )
        ):
            self.fail("Sweep does not fit the verified stage, attempts, or AP floor")
        frame = self.selected_quantity(frame, count)
        screen = frame.screen
        if (
            screen.cost != original.cost
            or screen.stars != 3
            or screen.ap != original.ap
            or screen.count != count
            or screen.after != screen.ap - count * screen.cost
            or screen.after < self.config.ap_floor
            or screen.target is None
        ):
            self.fail("Sweep projection changed; no AP spent")
        self.journal.save_image(
            f"before-sweep-{self.actions:04d}.png", frame.capture.png
        )
        self.tap(frame, screen.target, "Review AP sweep confirmation")
        confirmation = self.wait("confirm", predicate=lambda s: s.ap is not None)
        if (
            confirmation.screen.cost != count * screen.cost
            or confirmation.screen.count != count
            or confirmation.screen.ap != screen.ap
            or confirmation.screen.ap - confirmation.screen.cost < self.config.ap_floor
        ):
            self.fail("Sweep confirmation changed cost, count, or AP floor")
        self.state["pending"] = {
            "strategy": screen.strategy,
            "stage": screen.stage,
            "ap_before": screen.ap,
            "floor": self.config.ap_floor,
            "cost": screen.cost,
            "count": count,
            "next_stage": next_stage,
            "run_dir": str(self.run_dir),
        }
        self.save()
        self.important(
            "ap_sweep_requested",
            f"Sweep {screen.strategy} {screen.stage} ×{count}: {count*screen.cost} AP, {screen.ap} → {screen.after}; floor {self.config.ap_floor}.",
            stage=screen.stage,
            strategy=screen.strategy,
            count=count,
            ap_before=screen.ap,
            ap_cost=count * screen.cost,
        )
        self.tap(
            confirmation, confirmation.screen.target, "Confirm the verified AP cost"
        )
        receipt = self.wait("receipt", timeout=90, predicate=lambda s: s.ap is not None)
        receipt_name = f"sweep-receipt-{self.actions:04d}.png"
        self.journal.save_image(receipt_name, receipt.capture.png)
        if not screen.after <= receipt.screen.ap <= screen.after + 1:
            self.fail(
                "Sweep receipt AP differs from its projection; inspect possible level-up or interrupted results"
            )
        receipt = inspect_receipt(self, receipt, self.run_dir / receipt_name)
        rewards = receipt.screen.rewards
        self.tap(receipt, receipt.screen.target, "Close sweep receipt")
        result = self.wait(
            "detail",
            predicate=lambda s: s.strategy == screen.strategy
            and s.stage == screen.stage
            and s.ap is not None,
        )
        if (
            result.screen.ap < self.config.ap_floor
            or result.screen.ap < screen.after
            or screen.strategy == "elephs"
            and result.screen.remaining != screen.remaining - count
        ):
            self.fail("Post-sweep AP or remaining attempts did not verify")
        self.state["pending"] = None
        if next_stage is not None:
            self.state["next_stage"] = next_stage
        self.state["last_ap"] = result.screen.ap
        self.save()
        self.important(
            "ap_spent",
            f"Swept {screen.strategy} {screen.stage} ×{count}: {count*screen.cost} AP spent; {result.screen.ap} AP left. Receipt saved.",
            stage=screen.stage,
            strategy=screen.strategy,
            count=count,
            ap_spent=count * screen.cost,
            ap_before=screen.ap,
            ap_after=result.screen.ap,
            rewards=list(rewards),
            evidence=str(self.run_dir / receipt_name),
        )
        return result

    def run(self):
        self.state = read_state(self.config)
        if self.state["pending"]:
            self.fail(
                "An earlier AP sweep has an unresolved result; inspect its receipt before spending again"
            )
        if self.state["blocked_reason"] and not self.allow_retry:
            self.fail(
                "AP spending is paused after a failure; explicitly queue Spend AP to retry"
            )
        frame = self.wait("home", predicate=lambda s: s.ap is not None)
        summary = "AP is already at or below the floor"
        if frame.screen.ap > self.config.ap_floor:
            strategy = self.config.ap_strategy
            self.phase(
                f"Checking {strategy} sweeps; keep at least {self.config.ap_floor} AP"
            )
            if strategy != "elephs":
                best = self.survey_commission(strategy)
                self.state["commissions"][strategy] = best
                self.save()
                if best:
                    frame = self.commission_detail(strategy, best)
                    count = (
                        0
                        if frame.screen.count == 0 and frame.screen.cost is None
                        else sweep_count(
                            frame.screen.ap, self.config.ap_floor, frame.screen.cost
                        )
                    )
                    if count:
                        frame = self.sweep(frame, count)
                    summary = (
                        "Remaining AP cannot cover another commission above the floor"
                    )
                    self.go_home(frame)
                else:
                    summary = "No three-star commission is available"
            else:
                if self.state["surveyed_at"] is None:
                    self.state["hard_stages"] = self.survey_hard()
                    self.state["surveyed_at"] = self.wall_clock().isoformat()
                    self.save()
                order = (
                    default_order(self.state["hard_stages"])
                    if self.config.ap_hard_default_order
                    else self.config.ap_hard_order
                )
                if set(order) - set(self.state["hard_stages"]):
                    self.fail(
                        "Rotation includes unverified stages; rescan three-star clears"
                    )
                frame = self.enter_hard() if order else self.wait("home")
                unavailable = set()
                summary = "No eligible Hard stages have attempts left"
                for _ in range(len(order) * 4 + 1):
                    choice = choose_hard(order, self.state["next_stage"], unavailable)
                    if choice is None:
                        break
                    self.phase(f"Checking Hard {choice.stage}")
                    frame = self.hard_area(frame, choice.stage)
                    stage = next(s for s in frame.screen.stages if s.id == choice.stage)
                    if stage.stars != 3 or stage.target is None or stage.remaining == 0:
                        unavailable.add(choice.stage)
                        continue
                    self.tap(frame, stage.target, f"Inspect Hard {stage.id}")
                    detail = self.wait(
                        "detail",
                        predicate=lambda s: s.strategy == "elephs"
                        and s.stage == stage.id,
                    )
                    if (
                        detail.screen.count == 0
                        and detail.screen.remaining
                        and detail.screen.after == detail.screen.ap
                    ):
                        frame = detail
                        summary = "Remaining AP cannot cover another Hard sweep above the floor"
                        break
                    if detail.screen.cost is None or detail.screen.remaining == 0:
                        self.fail(
                            "Hard mission details changed while opening the stage"
                        )
                    if not sweep_count(
                        detail.screen.ap, self.config.ap_floor, detail.screen.cost, 1
                    ):
                        frame = detail
                        summary = "Remaining AP cannot cover another Hard sweep above the floor"
                        break
                    detail = self.sweep(detail, 1, next_stage=choice.next_stage)
                    self.tap(detail, (1127, 109), "Return to Hard stage list")
                    frame = self.wait("hard_list")
                else:
                    self.fail("Hard rotation exceeded the observed daily attempt limit")
                self.go_home(frame)
        else:
            self.home()
        frame = self.wait("home", predicate=lambda s: s.ap is not None)
        self.state.update(
            last_ap=frame.screen.ap,
            last_summary=summary,
            blocked_reason=None,
            next_check_at=(self.wall_clock() + timedelta(hours=1)).isoformat(),
        )
        self.save()
        self.phase(
            f"{summary}. {frame.screen.ap} AP left; floor {self.config.ap_floor}."
        )
        return self.finish()


class APScanRunner(APRunner):
    task = "scan_ap"

    def run(self):
        self.state = read_state(self.config)
        self.wait("home")
        self.survey()
        self.home()
        return self.finish()


def _run(config, device, startup, cls, **kwargs):
    with InstanceLock(config):
        runner = cls(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            if runner.state is not None and cls is APRunner:
                runner.state["blocked_reason"] = (
                    str(exc) or "AP job interrupted; review the saved receipt"
                )
                runner.save()
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()


def run_spend_ap(config, device, startup, **kwargs):
    return _run(config, device, startup, APRunner, **kwargs)


def run_scan_ap(config, device, startup, **kwargs):
    return _run(config, device, startup, APScanRunner, **kwargs)
