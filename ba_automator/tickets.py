"""Serial daily Bounty/Scrimmage sweeps, with weekday-rotated remainder tickets."""

from .actions import record_action
from .club import game_day
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .ticket_state import AREAS, allocation, read_state, write_state
from .ticket_vision import TicketVision


class TicketRunner(ShopRunner):
    task = "bounties"

    def __init__(self, config, device, startup, *, vision=None, **kwargs):
        super().__init__(
            config, device, startup, vision=vision or TicketVision(startup), **kwargs
        )
        self.state = None

    def budget(self):
        if self.clock() - self.started > 1200 or self.actions >= 500:
            self.fail("Ticket task reached its time or input limit")

    def save(self):
        write_state(self.config, self.task, self.state)

    def important(self, action, detail, **fields):
        record_action(self.config, action, detail, task=self.task, **fields)
        self.phase(detail)

    def check_day(self):
        if game_day(self.wall_clock()) != self.state["day"]:
            self.fail(
                "Daily reset occurred during the ticket job; queue it again for the new day"
            )

    def enter_menu(self):
        self.navigate("home", "campaign", (1200, 641))
        return self.navigate(
            "campaign", "menu", (735, 400) if self.task == "bounties" else (710, 608)
        )

    def enter_area(self, area):
        frame = self.wait("menu", predicate=lambda s: s.task == self.task)
        index = AREAS[self.task].index(area)
        self.tap(
            frame,
            (1080, (205 + index * (114 if self.task == "bounties" else 109))),
            f"Inspect {area}",
        )
        return self.wait(
            "list", predicate=lambda s: s.task == self.task and s.area == area
        )

    def swipe(self, frame, down):
        if (
            frame.screen.kind != "list"
            or not frame.capture.is_fresh(self.clock())
            or self.device.foreground_package() != self.config.package
        ):
            self.fail("No fresh ticket stage list for scrolling")
        self.budget()
        points = ((945, 295), (945, 535)) if down else ((945, 535), (945, 295))
        self.journal.record("intent", operation="swipe", points=points)
        if not self.device.swipe(
            *points,
            duration_ms=650,
            deadline=frame.capture.deadline,
            monotonic=self.clock,
        ):
            self.fail("Ticket list scroll expired")
        self.actions += 1
        self.sleep(1)

    def top(self, frame):
        for _ in range(12):
            if any(s.id == "A" for s in frame.screen.stages):
                return frame
            self.swipe(frame, True)
            frame = self.wait("list", predicate=lambda s: s.task == self.task)
        self.fail("Could not find the first ticket stage")

    def survey(self, frame):
        area = frame.screen.area
        frame = self.top(frame)
        seen, previous, stationary = {}, None, 0
        for _ in range(24):
            signature = tuple(
                (s.id, s.stars, s.target is not None) for s in frame.screen.stages
            )
            for s in frame.screen.stages:
                if s.id in seen and seen[s.id].stars != s.stars:
                    self.fail("Stage stars changed during the ticket survey")
                seen[s.id] = s
            stationary = stationary + 1 if signature == previous else 0
            if stationary >= 2:
                break
            previous = signature
            self.swipe(frame, False)
            frame = self.wait(
                "list", predicate=lambda s: s.task == self.task and s.area == area
            )
        else:
            self.fail("Ticket stage list did not reach a stable end")
        ids = sorted(seen)
        if not ids or ids != [chr(n) for n in range(65, ord(ids[-1]) + 1)]:
            self.fail("Ticket survey missed a stage; no tickets spent")
        eligible = [
            s.id for s in seen.values() if s.stars == 3 and s.target is not None
        ]
        self.journal.record(
            "survey", area=area, stars={s.id: s.stars for s in seen.values()}
        )
        self.journal.save_image(
            f"survey-{AREAS[self.task].index(area)}.png", frame.capture.png
        )
        return frame, max(eligible) if eligible else None

    def open_detail(self, frame, stage):
        area = frame.screen.area
        frame = self.top(frame)
        for _ in range(18):
            row = next((s for s in frame.screen.stages if s.id == stage), None)
            if row:
                if row.stars != 3 or row.target is None:
                    self.fail("Selected stage is no longer a three-star clear")
                self.tap(frame, row.target, f"Open {area} {stage}")
                return self.wait(
                    "detail",
                    predicate=lambda s: (s.task, s.area, s.stage)
                    == (self.task, area, stage),
                )
            self.swipe(frame, False)
            frame = self.wait(
                "list", predicate=lambda s: s.task == self.task and s.area == area
            )
        self.fail("Selected ticket stage could not be located")

    def quantity(self, frame, wanted):
        identity = (frame.screen.task, frame.screen.area, frame.screen.stage)
        # Min then small increments gives every requested count an observed result.
        if frame.screen.count > wanted:
            self.tap(frame, (788, 300), "Set sweep quantity to minimum")
            frame = self.wait(
                "detail",
                predicate=lambda s: (s.task, s.area, s.stage) == identity
                and s.count == 1,
            )
        while frame.screen.count < wanted:
            expected = frame.screen.count + 1
            self.tap(frame, (1015, 300), f"Set sweep quantity to {expected}")
            frame = self.wait(
                "detail",
                predicate=lambda s: (s.task, s.area, s.stage) == identity
                and s.count == expected,
            )
        return frame

    def sweep(self, frame, count, index):
        self.check_day()
        before = frame.screen
        quota_left = self.state["quotas"][index] - self.state["done"][index]
        if (
            before.task != self.task
            or before.area != AREAS[self.task][index]
            or before.stars != 3
            or before.ap is None
            or before.tickets is None
            or before.ap_cost is None
            or before.target is None
            or count < 1
            or count > min(quota_left, before.tickets)
            or (
                before.ap_cost > 0
                and before.ap - count * before.ap_cost < self.config.ap_floor
            )
        ):
            self.fail(
                "Ticket sweep violates its allocation, three-star clear, or AP floor"
            )
        frame = self.quantity(frame, count)
        s = frame.screen
        if (
            (s.task, s.area, s.stage, s.ap, s.tickets, s.ap_cost, s.stars)
            != (
                before.task,
                before.area,
                before.stage,
                before.ap,
                before.tickets,
                before.ap_cost,
                3,
            )
            or s.count != count
            or s.after_tickets != s.tickets - count
            or s.after_ap != s.ap - count * s.ap_cost
            or s.target is None
        ):
            self.fail("Ticket sweep projection changed; no tickets spent")
        self.journal.save_image(f"before-{index}.png", frame.capture.png)
        self.tap(frame, s.target, "Review ticket sweep confirmation")
        confirm = self.wait("confirm", predicate=lambda c: c.task == self.task)
        c = confirm.screen
        if (c.ap, c.tickets, c.count, c.ap_cost) != (
            s.ap,
            count,
            count,
            count * s.ap_cost,
        ):
            self.fail("Ticket confirmation changed cost or quantity; no tickets spent")
        self.check_day()
        self.state["pending"] = dict(
            area=s.area,
            stage=s.stage,
            count=count,
            tickets_before=s.tickets,
            ap_before=s.ap,
            ap_cost=c.ap_cost,
            run_dir=str(self.run_dir),
        )
        self.save()
        self.important(
            "ticket_sweep_requested",
            f"{s.area} {s.stage} ×{count}: {count} tickets, {c.ap_cost} AP. Waiting for receipt.",
            area=s.area,
            stage=s.stage,
            count=count,
            ap_cost=c.ap_cost,
        )
        self.tap(confirm, c.target, "Confirm verified ticket sweep")
        receipt = self.wait("receipt", timeout=90, predicate=lambda r: r.ap is not None)
        self.journal.save_image(f"receipt-{index}.png", receipt.capture.png)
        if (
            receipt.screen.task != self.task
            or receipt.screen.count != count
            or not s.after_ap <= receipt.screen.ap <= s.after_ap + 1
        ):
            self.fail(
                "Ticket receipt does not match the requested sweep; inspect before retrying"
            )
        self.tap(receipt, receipt.screen.target, "Close ticket sweep receipt")
        # Spending the last ticket closes Mission Info automatically.
        result = self.wait(
            {"detail", "list"} if s.after_tickets == 0 else "detail",
            predicate=lambda r: (r.task, r.area) == (s.task, s.area)
            and (r.kind == "list" and s.after_tickets == 0 or r.stage == s.stage),
        )
        if (
            result.screen.tickets != s.after_tickets
            or result.screen.ap is None
            or not s.after_ap <= result.screen.ap <= s.after_ap + 1
        ):
            self.fail("Post-sweep ticket or AP balance did not verify")
        self.state["done"][index] += count
        self.state["pending"] = None
        self.save()
        self.important(
            "tickets_spent",
            f"{s.area} {s.stage} ×{count} done. {result.screen.tickets} tickets left; {c.ap_cost} AP spent. Receipt saved.",
            area=s.area,
            stage=s.stage,
            count=count,
            ap_spent=c.ap_cost,
            tickets_before=s.tickets,
            tickets_after=result.screen.tickets,
            rewards=list(receipt.screen.rewards),
            evidence=str(self.run_dir / f"receipt-{index}.png"),
        )
        return result

    def menu_from(self, frame):
        if frame.screen.kind == "detail":
            self.tap(frame, (1127, 140), "Close ticket stage details")
            frame = self.wait("list", predicate=lambda s: s.task == self.task)
        self.tap(frame, (55, 35), "Return to ticket areas")
        return self.wait("menu", predicate=lambda s: s.task == self.task)

    def run(self):
        self.state = read_state(self.config, self.task)
        if self.state["pending"]:
            self.fail(
                "An earlier ticket sweep has an unresolved receipt; inspect it before spending again"
            )
        self.wait("home")
        menu = self.enter_menu()
        if menu.screen.task != self.task:
            self.fail("Wrong ticket task opened")
        day = game_day(self.wall_clock())
        if self.state["day"] != day:
            self.state.update(
                day=day,
                total=menu.screen.tickets,
                quotas=allocation(menu.screen.tickets, day),
                done=[0, 0, 0],
            )
            self.save()
            self.important(
                "ticket_allocation",
                f"{menu.screen.tickets} tickets for {day}: "
                + ", ".join(
                    f"{a} {n}" for a, n in zip(AREAS[self.task], self.state["quotas"])
                ),
                quotas=self.state["quotas"],
                game_day=day,
            )
        for index, area in enumerate(AREAS[self.task]):
            self.check_day()
            wanted = self.state["quotas"][index] - self.state["done"][index]
            if wanted <= 0:
                continue
            if menu.screen.tickets < sum(self.state["quotas"]) - sum(
                self.state["done"]
            ):
                self.fail(
                    "Tickets changed outside this allocation; inspect before redistributing them"
                )
            frame, best = self.survey(self.enter_area(area))
            if best is None:
                self.important(
                    "ticket_area_skipped",
                    f"{area}: no three-star stage; keeping its {wanted} tickets.",
                    area=area,
                )
                menu = self.menu_from(frame)
                continue
            frame = self.open_detail(frame, best)
            s = frame.screen
            affordable = (
                wanted
                if s.ap_cost == 0
                else (
                    max(0, s.ap - self.config.ap_floor) // s.ap_cost if s.ap_cost else 0
                )
            )
            count = min(wanted, affordable)
            if count:
                frame = self.sweep(frame, count, index)
            else:
                self.important(
                    "ticket_area_skipped",
                    f"{area}: keeping {wanted} tickets to preserve the AP floor.",
                    area=area,
                )
            menu = self.menu_from(frame)
        self.tap(menu, (1237, 24), "Return home after ticket sweeps")
        self.home()
        left = sum(self.state["quotas"]) - sum(self.state["done"])
        self.phase(
            f'{sum(self.state["done"])} of {self.state["total"]} allocated tickets used; {left} reserved tickets remain.'
        )
        return self.finish()


class ScrimmageRunner(TicketRunner):
    task = "scrimmages"


def _run(config, device, startup, cls, **kwargs):
    with InstanceLock(config):
        runner = cls(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()


def run_bounties(config, device, startup, **kwargs):
    return _run(config, device, startup, TicketRunner, **kwargs)


def run_scrimmages(config, device, startup, **kwargs):
    return _run(config, device, startup, ScrimmageRunner, **kwargs)
