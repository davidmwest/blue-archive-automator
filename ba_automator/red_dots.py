"""Read home badges and leave a bounded queue request for the local daemon."""

import json
from .home_badges import BadgeVision, PRIORITY
from .locking import InstanceLock
from .shop_runtime import ShopRunner


class RedDotsRunner(ShopRunner):
    task = "red_dots"

    def __init__(self, config, device, startup, **kwargs):
        kwargs.setdefault("vision", BadgeVision(startup))
        super().__init__(config, device, startup, **kwargs)

    def run(self):
        first = self.wait("home")
        self.sleep(1)
        second = self.wait("home")
        tasks = [
            task
            for task in PRIORITY
            if task in first.screen.badges and task in second.screen.badges
        ]
        amounts = (first.screen.ap, second.screen.ap)
        ap = (
            min(amounts)
            if all(type(a) is int for a in amounts)
            and abs(amounts[0] - amounts[1]) <= 1
            else None
        )
        self.journal.save_image("home.png", second.capture.png)
        self.journal.record("badges", tasks=tasks)
        # The parent consumes this only after a successful child exit, below
        # that child's private run root; it never accepts arbitrary job names.
        (self.run_dir / "requests.json").write_text(
            json.dumps({"version": 1, "tasks": tasks, "ap": ap})
        )
        self.phase("Home notifications: " + (", ".join(tasks) or "none"))
        return self.finish()


def run_red_dots(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = RedDotsRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record("finished", status="failed", detail=str(exc))
            raise
        finally:
            runner.journal.close()
