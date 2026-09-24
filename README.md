# Blue Archive automator

we're making a custom blue archive automator. the idea is to handle the daily stuff, keep resource use reasonable, and have the same core work on mac and windows.

BlueStacks + ADB looks like the right starting point. [ALAS](https://github.com/LmeSzinc/AzurLaneAutoScript) is the model for how to structure it: recognize the screen, do one thing, check what happened, and recover when the game gets stuck.

## the plan

- BlueStacks Air on mac, BlueStacks 5 on windows.
- ADB for screenshots, taps, swipes, and app control.
- Run alongside the Azur Lane daemon, with each one pointed at its own instance and ADB port.
- Python for the automation engine, with OpenCV for screen recognition and OCR where we actually need to read text or numbers.
- Small task modules for things like cafe, daily rewards, and sweeps.
- A scheduler that remembers what's done and sleeps between runs.
- A local dashboard eventually, once the underlying automation works.

we'll borrow the useful ideas from ALAS and build a smaller core around Blue Archive. emulator-specific setup stays separate so the game logic can travel between platforms.

## first things first

1. Find the right instance and verify the connection.
2. Capture screens and build recognition tests from them.
3. Get reliable navigation working.
4. Automate one small daily task all the way through.
5. Add scheduling and the rest of the daily loop.

there's a dedicated Blue Archive instance for staging, but it's logged into a real, mature account. that means we start with observation and screenshot replay, then add live actions carefully. spending pyroxenes and pulling on banners are outside the initial scope.

## where things stand

architecture first. this repo has the design and build order; there's no runnable automator yet. ADB communication works with the currently running BlueStacks Air instance on the development mac. the Blue Archive instance is configured, but its screenshot and input paths still need testing. windows also needs its own smoke test.

CPU and memory allocation are useful knobs. resolution, graphics settings, and FPS let us tune the graphics workload. we'll measure actual usage before calling any preset efficient.

see [the architecture](docs/architecture.md) and [the build order](docs/roadmap.md).
