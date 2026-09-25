"""Command line entry points; daily always starts with restart."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import logging
from pathlib import Path
import sys

from .adb import AdbDevice
from .config import Config
from .daily_log import append_event, close_daily_logging, configure_daily_logging
from .locking import InstanceLock
from .tasks import TASKS, task_plan
from .vision import StartupVision, decode_frame


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ba", description="Blue Archive local task automation"
    )
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="Open the local control server and serial job queue"
    )
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser(
        "probe", help="Verify the selected ADB device and installed game"
    )
    capture = commands.add_parser(
        "capture", help="Save a frame without sending game input"
    )
    capture.add_argument("--output", type=Path, default=Path("data/capture.png"))
    inspect = commands.add_parser(
        "inspect", help="Classify a saved or live frame without game input"
    )
    inspect.add_argument("--image", type=Path)
    descriptions = {
        "total_assault": "Mock-test the configured Total Assault difficulty before a real clear, then sweep remaining tickets",
        "assault_rewards": "Collect available Total Assault rank and points rewards without entering a battle",
        "tactical_rewards": "Collect Tactical Challenge time and daily rewards without fighting",
        "red_dots": "Check home and Campaign notifications for daemon collection jobs",
        "free_pack": "Claim the Free daily pack when its home badge is visible, then collect mail",
        "tasks": "Collect completed Tasks rewards when the home red dot is visible",
        "bounties": "Split available Bounty tickets across the three highest three-star clears",
        "scrimmages": "Split Scrimmage tickets across three schools while respecting the AP floor",
        "restart": "Restart Blue Archive and reach the home screen",
        "club": "Check Social → Club attendance and collect its mail",
        "cafe": "Restart, collect cafe earnings, and greet students",
        "crafting": "Collect finished crafts, fill Quick Craft slots, and save their timers",
        "packs": "Check optional paid pack renewals, then collect mail",
        "mail": "Collect ordinary and product mail and log rewards",
        "spend_ap": "Sweep selected Hard missions or commissions down to the AP floor",
        "scan_ap": "Survey three-star Hard missions and commissions without spending AP",
        "lessons": "Restart and use lesson tickets with the configured strategy",
        "daily": "Run restart, free package, Club, enabled packs, mail, cafe, enabled ticket sweeps, Tactical Challenge rewards, lessons, optional Total Assault, raid rewards, AP spending and task rewards",
    }
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description)
        command.add_argument(
            "--no-downloads",
            action="store_true",
            help="Stop if game-data download consent is needed",
        )
        if name == "packs":
            command.add_argument(
                "--retry-packs",
                action="store_true",
                help="Explicitly retry a blocked pack check; unresolved charges still cannot be repeated",
            )
        if name == "spend_ap":
            command.add_argument(
                "--retry-ap",
                action="store_true",
                help="Retry a failed AP job; unresolved sweeps remain blocked",
            )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )

    daily_handler = None
    command_status = "success"
    try:
        if not (args.command == "inspect" and args.image):
            config = Config.from_file(args.config)
            daily_handler = configure_daily_logging(config.state_dir)
            append_event(config.state_dir, "command_started", task=args.command)
        if args.command == "serve":
            from .server import serve

            if not 1 <= args.port <= 65535:
                raise RuntimeError("Server port must be between 1 and 65535")
            serve(args.config, port=args.port)
            return 0
        if args.command == "inspect" and args.image:
            observation = StartupVision().analyze(args.image.read_bytes())
            print(json.dumps(asdict(observation), indent=2))
            return 0

        device = AdbDevice(config)
        if args.command in TASKS:
            from .restart import run_restart
            from .cafe import run_cafe

            if args.no_downloads:
                config = replace(config, auto_download=False)
                device = AdbDevice(config)
            vision = None
            for task in task_plan(args.command, config):
                if vision is None:
                    vision = StartupVision()
                if task == "total_assault":
                    from .total_assault import run_total_assault

                    runner = run_total_assault
                elif task == "assault_rewards":
                    from .assault_rewards import run_assault_rewards

                    runner = run_assault_rewards
                elif task == "tactical_rewards":
                    from .tactical_rewards import run_tactical_rewards

                    runner = run_tactical_rewards
                elif task == "club":
                    from .club import run_club

                    runner = run_club
                elif task == "red_dots":
                    from .red_dots import run_red_dots

                    runner = run_red_dots
                elif task == "free_pack":
                    from .free_pack import run_free_pack

                    runner = run_free_pack
                elif task == "tasks":
                    from .task_rewards import run_task_rewards

                    runner = run_task_rewards
                elif task in {"spend_ap", "scan_ap"}:
                    from .spend_ap import run_spend_ap, run_scan_ap

                    runner = run_spend_ap if task == "spend_ap" else run_scan_ap
                elif task in {"bounties", "scrimmages"}:
                    from .tickets import run_bounties, run_scrimmages

                    runner = run_bounties if task == "bounties" else run_scrimmages
                elif task == "packs":
                    from .packs import run_packs

                    runner = run_packs
                elif task == "mail":
                    from .mail import run_mail

                    runner = run_mail
                elif task == "crafting":
                    from .crafting import run_crafting

                    runner = run_crafting
                elif task == "lessons":
                    from .lessons import run_lessons

                    runner = run_lessons
                else:
                    runner = run_restart if task == "restart" else run_cafe
                options = (
                    {"allow_retry": getattr(args, "retry_packs", False)}
                    if task == "packs"
                    else {}
                )
                if task == "spend_ap":
                    options = {"allow_retry": getattr(args, "retry_ap", False)}
                append_event(
                    config.state_dir, "task_dispatched", task=task, command=args.command
                )
                result = runner(config, device, vision, **options)
                append_event(
                    config.state_dir,
                    "task_result",
                    task=task,
                    run=result.run_dir.name,
                    status=result.status,
                    duration=result.duration,
                    actions=result.actions,
                )
                print(json.dumps(asdict(result), default=str, indent=2))
                if result.status != "success":
                    command_status = result.status
                    break
            return 0

        with InstanceLock(config):
            device.connect()
            device.verify_package()
            size = device.display_size()
            if size != (1280, 720):
                raise RuntimeError(
                    f"Set the selected instance to 1280×720; Android reports {size}"
                )
            if args.command == "probe":
                print(
                    json.dumps(
                        {
                            "serial": config.serial,
                            "package": config.package,
                            "display": size,
                            "foreground": device.foreground_package(),
                        },
                        indent=2,
                    )
                )
            else:
                png = device.screenshot()
                decode_frame(png)
                if args.command == "capture":
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_bytes(png)
                    print(args.output.resolve())
                else:
                    print(json.dumps(asdict(StartupVision().analyze(png)), indent=2))
        return 0
    except KeyboardInterrupt:
        command_status = "stopped"
        logging.getLogger(__name__).warning(
            "Command interrupted; no further game input will be sent"
        )
        print("Stopped. No further game input will be sent.", file=sys.stderr)
        return 130
    except (RuntimeError, OSError) as exc:
        command_status = "failed"
        logging.getLogger(__name__).error("Command %s failed: %s", args.command, exc)
        print(f"Error: {exc}", file=sys.stderr)
        run_dir = getattr(exc, "run_dir", None)
        if run_dir:
            print(f"Local diagnostics: {run_dir}", file=sys.stderr)
        return 1
    except Exception:
        command_status = "failed"
        logging.getLogger(__name__).exception(
            "Command %s failed unexpectedly", args.command
        )
        raise
    finally:
        if daily_handler is not None:
            append_event(
                daily_handler.state_dir,
                "command_finished",
                task=args.command,
                status=command_status,
                level="ERROR" if command_status == "failed" else "INFO",
            )
            close_daily_logging(daily_handler)


if __name__ == "__main__":
    raise SystemExit(main())
