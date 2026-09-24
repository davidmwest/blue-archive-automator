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
from .locking import InstanceLock
from .tasks import TASKS, task_plan
from .vision import StartupVision, decode_frame


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ba", description="Blue Archive local task automation")
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the local control server and serial job queue")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("probe", help="Verify the selected ADB device and installed game")
    capture = commands.add_parser("capture", help="Save a frame without sending game input")
    capture.add_argument("--output", type=Path, default=Path("data/capture.png"))
    inspect = commands.add_parser("inspect", help="Classify a saved or live frame without game input")
    inspect.add_argument("--image", type=Path)
    descriptions = {"restart": "Restart Blue Archive and reach the home screen",
                    "club": "Club placeholder: awaiting a fresh daily-reset test (no game input)",
                    "cafe": "Restart, collect cafe earnings, and greet students",
                    "lessons": "Restart and use lesson tickets with the configured strategy",
                    "daily": "Run restart, the Club placeholder, cafe, and enabled lessons in order"}
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description)
        command.add_argument("--no-downloads", action="store_true", help="Stop if game-data download consent is needed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    try:
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

        config = Config.from_file(args.config)
        device = AdbDevice(config)
        if args.command in TASKS:
            from .restart import run_restart
            from .cafe import run_cafe

            if args.no_downloads:
                config = replace(config, auto_download=False)
                device = AdbDevice(config)
            vision = None
            for task in task_plan(args.command, config):
                if task == "club":
                    from .club import run_club

                    result = run_club(config)
                    print(json.dumps(asdict(result), default=str, indent=2))
                    continue
                if vision is None:
                    vision = StartupVision()
                if task == "lessons":
                    from .lessons import run_lessons

                    runner = run_lessons
                else:
                    runner = run_restart if task == "restart" else run_cafe
                result = runner(config, device, vision)
                print(json.dumps(asdict(result), default=str, indent=2))
            return 0

        with InstanceLock(config):
            device.connect()
            device.verify_package()
            size = device.display_size()
            if size != (1280, 720):
                raise RuntimeError(f"Set the selected instance to 1280×720; Android reports {size}")
            if args.command == "probe":
                print(json.dumps({"serial": config.serial, "package": config.package,
                                  "display": size, "foreground": device.foreground_package()}, indent=2))
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
        print("Stopped. No further game input will be sent.", file=sys.stderr)
        return 130
    except (RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        run_dir = getattr(exc, "run_dir", None)
        if run_dir:
            print(f"Local diagnostics: {run_dir}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
