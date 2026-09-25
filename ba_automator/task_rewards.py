"""Claim completed Tasks rewards only when the home notification is visible."""

import cv2
import numpy as np
from .actions import record_action
from .loot_receipts import inspect_receipt
from .vision import decode_frame
from .locking import InstanceLock
from .shop_runtime import ShopRunner
from .task_rewards_vision import ALL_TAB, HOME_BUTTON, HOME_TASKS, TaskRewardsVision

MAX_CLAIMS = 12


class TaskRewardsRunner(ShopRunner):
    task = "tasks"

    def __init__(self, config, device, startup, *, vision=None, **kwargs):
        super().__init__(
            config,
            device,
            startup,
            vision=vision or TaskRewardsVision(startup),
            **kwargs,
        )

    def enter(self):
        for _ in range(3):
            frame = self.wait({"home", "tasks_other", "tasks", "tasks_empty"})
            if frame.screen.kind in {"tasks", "tasks_empty"}:
                return frame
            target = HOME_TASKS if frame.screen.kind == "home" else ALL_TAB
            self.tap(
                frame,
                target,
                "Open Tasks" if frame.screen.kind == "home" else "Select All tasks",
            )
            self.sleep(1)
        return self.wait({"tasks", "tasks_empty"})

    def collect(self, frame):
        label = frame.screen.claim
        self.journal.save_image(
            f"claim-{self.actions:03d}-before.png", frame.capture.png
        )
        record_action(
            self.config,
            "task_rewards_claim_requested",
            f"claiming {label} task rewards.",
            task=self.task,
        )
        self.tap(frame, frame.screen.target, f"Claim {label} task rewards")
        # A claim is never blindly retried. Require its receipt before continuing.
        receipt = self.wait("receipt")
        receipt = self.log_receipt(receipt)
        self.tap(receipt, receipt.screen.target, "Close Tasks reward receipt")
        return self.wait({"tasks", "tasks_empty"})

    def pan_receipt(self, frame, *, to_start):
        if frame.screen.kind != "receipt" or not frame.capture.is_fresh(self.clock()):
            self.fail("No fresh Tasks receipt for scrolling")
        if self.device.foreground_package() != self.config.package:
            self.fail("Foreground changed before reading Tasks rewards")
        self.budget()
        points = ((300, 370), (1000, 370)) if to_start else ((1000, 370), (300, 370))
        self.journal.record(
            "intent", operation="swipe", points=points, detail="Read Tasks receipt"
        )
        if not self.device.swipe(
            *points,
            duration_ms=900,
            deadline=frame.capture.deadline,
            monotonic=self.clock,
        ):
            self.fail("Tasks receipt scroll expired")
        self.actions += 1
        self.sleep(1)
        return self.wait("receipt")

    @staticmethod
    def same_page(before, after):
        return (
            bool(before.screen.items)
            and before.screen.items == after.screen.items
            and len(before.screen.positions) == len(after.screen.positions)
            and all(
                abs(a - b) <= 3
                for a, b in zip(before.screen.positions, after.screen.positions)
            )
        )

    def log_receipt(self, frame):
        receipt_id = f"receipt-{self.actions:03d}"
        self.journal.save_image(f"{receipt_id}-initial.png", frame.capture.png)
        if getattr(self.vision, 'startup', None) is not None:
            evidence = f"{receipt_id}.png"
            self.journal.save_image(evidence, frame.capture.png)
            return inspect_receipt(self, frame, self.run_dir / evidence)
        # Receipts animate toward the final item. Rewind, then scan overlapping
        # pages. Repeated names are the same aggregated card, never another drop.
        stationary = 0
        for _ in range(12):
            after = self.pan_receipt(frame, to_start=True)
            stationary = stationary + 1 if self.same_page(frame, after) else 0
            frame = after
            if stationary >= 2:
                break
        else:
            self.fail("Tasks receipt did not reach a readable beginning")
        items, panels, stationary = {}, [], 0
        for index in range(16):
            self.journal.save_image(
                f"{receipt_id}-page-{index:02d}.png", frame.capture.png
            )
            page = {item["name"]: item["quantity"] for item in frame.screen.items}
            if not page or (items and not set(items).intersection(page)):
                self.fail(
                    "Tasks receipt has unreadable or missing cards; saved its pages for review"
                )
            for name, quantity in page.items():
                if name in items and items[name] != quantity:
                    self.fail("A Tasks receipt quantity changed while scrolling")
                items[name] = quantity
            if not stationary:
                panels.append(decode_frame(frame.capture.png)[227:504].copy())
            after = self.pan_receipt(frame, to_start=False)
            stationary = stationary + 1 if self.same_page(frame, after) else 0
            frame = after
            if stationary >= 2:
                break
        else:
            self.fail("Tasks receipt did not reach a readable end")
        # One evidence image shows every scanned panel in the loot gallery.
        composite = np.concatenate(panels, axis=0)
        ok, encoded = cv2.imencode(".png", composite)
        if not ok:
            self.fail("Could not save Tasks reward evidence")
        evidence = f"{receipt_id}.png"
        self.journal.save_image(evidence, encoded.tobytes())
        received = [
            {"name": name, "quantity": quantity} for name, quantity in items.items()
        ]
        label = ", ".join(f'+{item["quantity"]:,} {item["name"]}' for item in received)
        detail = f"collected task rewards: {label}."
        record_action(
            self.config,
            "task_rewards_received",
            detail,
            task=self.task,
            items=received,
            evidence=str(self.run_dir / evidence),
            items_complete=False,
        )
        self.phase(detail)
        return frame

    def run(self):
        frame = self.wait("home")
        if not frame.screen.red_dot:
            # Recheck to avoid mistaking a badge animation for an empty task list.
            self.sleep(1)
            frame = self.wait("home")
            if not frame.screen.red_dot:
                self.phase("no Tasks red dot. nothing to collect.")
                self.home()
                return self.finish()
        return self.finish_collection(self.enter())

    def finish_collection(self, frame):
        for claim_index in range(MAX_CLAIMS + 1):
            if frame.screen.kind == "tasks_empty":
                self.sleep(1)
                frame = self.wait({"tasks", "tasks_empty"})
                if frame.screen.kind == "tasks_empty":
                    break
            if claim_index == MAX_CLAIMS:
                self.fail(
                    "Tasks collection reached its claim limit; inspect the receipts"
                )
            frame = self.collect(frame)
        self.tap(frame, HOME_BUTTON, "Return home from Tasks")
        self.home()
        final = self.wait("home")
        if final.screen.red_dot:
            self.fail(
                "Tasks still has a red dot after collection; inspect the local trace"
            )
        self.phase("task rewards collected. home is clear.")
        return self.finish()


def run_task_rewards(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = TaskRewardsRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record(
                "finished",
                status=(
                    "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
                ),
                detail=str(exc),
                frame=runner.last_frame,
            )
            raise
        finally:
            runner.journal.close()
