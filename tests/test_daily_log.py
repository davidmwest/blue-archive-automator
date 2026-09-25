from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ba_automator import daily_log
from ba_automator.actions import record_action
from ba_automator.runtime import Journal


def test_rollover_uses_local_calendar_date_and_offset(tmp_path, monkeypatch):
    moments = iter(
        [
            datetime(2026, 9, 24, 23, 59, 59, tzinfo=timezone(timedelta(hours=-7))),
            datetime(2026, 9, 25, 0, 0, 1, tzinfo=timezone(timedelta(hours=-7))),
        ]
    )
    monkeypatch.setattr(daily_log, "local_now", lambda: next(moments))
    daily_log.append_event(tmp_path, "before_midnight")
    daily_log.append_event(tmp_path, "after_midnight")
    assert daily_log.list_days(tmp_path) == ["2026-09-25", "2026-09-24"]
    assert "2026-09-24T23:59:59.000-07:00" in daily_log.read_day(tmp_path, "2026-09-24")
    assert "after_midnight" not in daily_log.read_day(tmp_path, "2026-09-24")
    assert "after_midnight" in daily_log.read_day(tmp_path, "2026-09-25")


def test_handler_reconfiguration_appends_and_skips_child_mirror(tmp_path):
    logger = logging.getLogger("ba_automator.tests")
    handler = daily_log.configure_daily_logging(tmp_path)
    try:
        assert daily_log.configure_daily_logging(tmp_path) is handler
        logger.info("first process")
        logger.info("child stdout duplicate", extra={"skip_daily": True})
        logging.getLogger("third_party_ocr").warning("private raw screen words")
    finally:
        daily_log.close_daily_logging(handler)
    handler = daily_log.configure_daily_logging(tmp_path)
    try:
        logger.info("after daemon restart")
    finally:
        daily_log.close_daily_logging(handler)
    text = daily_log.read_day(tmp_path, daily_log.today())
    assert text.count("first process") == 1
    assert text.count("after daemon restart") == 1
    assert "duplicate" not in text
    assert "private raw" not in text


def test_multiple_processes_append_complete_lines(tmp_path):
    script = """
from pathlib import Path
import sys
from ba_automator.daily_log import append_event
for number in range(24):
    append_event(Path(sys.argv[1]), 'worker_event', task=sys.argv[2], number=number, detail='X' * 9000)
"""
    children = []
    try:
        for index in range(4):
            children.append(
                subprocess.Popen(
                    [sys.executable, "-c", script, str(tmp_path), f"worker{index}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for child in children:
            output, error = child.communicate(timeout=30)
            assert child.returncode == 0, (output, error)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
        for child in children:
            child.communicate(timeout=5)
    lines = daily_log.read_day(tmp_path, daily_log.today()).splitlines()
    assert len(lines) == 96
    for worker in range(4):
        found = [line for line in lines if f"[worker{worker}/-]" in line]
        assert len(found) == 24
        assert all(line.count("X") == 9000 for line in found)
        assert {int(line.rsplit("number=", 1)[1]) for line in found} == set(range(24))


@pytest.mark.parametrize(
    "day",
    [
        "../../config/local.toml",
        "2026-9-24",
        "2026-02-30",
        "2026-09-24.log",
        "2026-09-24\n",
        "",
        "２０２６-０９-２４",
        None,
    ],
)
def test_read_rejects_non_calendar_paths(tmp_path, day):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        daily_log.read_day(tmp_path, day)


def test_read_missing_is_safe_and_listing_ignores_other_files(tmp_path):
    with pytest.raises(FileNotFoundError) as error:
        daily_log.read_day(tmp_path, "2026-09-24")
    assert str(tmp_path) not in str(error.value)
    directory = tmp_path / "logs"
    directory.mkdir()
    for name in (
        "2026-09-24.log",
        "2026-09-23.log",
        "2026-02-30.log",
        "secret.log",
        "2026-09-22.log.tmp",
    ):
        (directory / name).write_text("test")
    assert daily_log.list_days(tmp_path) == ["2026-09-24", "2026-09-23"]


def test_reads_and_writes_reject_symlinks(tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("secret")
    directory = tmp_path / "logs"
    directory.mkdir()
    try:
        (directory / "2026-09-24.log").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    with pytest.raises(ValueError, match="symbolic link"):
        daily_log.read_day(tmp_path, "2026-09-24")
    assert daily_log.list_days(tmp_path) == []
    other = tmp_path / "other"
    other.mkdir()
    (other / "logs").symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        daily_log.read_day(other, "2026-09-24")


def test_private_data_is_removed_but_game_facts_remain(tmp_path):
    daily_log.append_event(
        tmp_path,
        "loot_received",
        task="mail",
        detail="Received loot; user@example.com password=do-not-log token:secretbearer 4242 4242 4242 4242\nforged-line",
        text=["private OCR"],
        ocr_text="private OCR",
        words=["raw words"],
        screenshot=b"PNG",
        credentials={"password": "private password"},
        items=[
            {"name": "Pyroxene", "quantity": 40, "account_email": "private@secret.com"}
        ],
        billing_details={"payment_method": "secret VISA"},
        frame="trace-01.png",
        nested={"safe": 4, "cookie": "session secret", "card_number": "1234"},
    )
    text = daily_log.read_day(tmp_path, daily_log.today())
    for secret in (
        "user@example.com",
        "do-not-log",
        "secretbearer",
        "4242",
        "private OCR",
        "raw words",
        "private password",
        "private@secret.com",
        "secret VISA",
        "session secret",
        "1234",
    ):
        assert secret not in text
    assert len(text.splitlines()) == 1
    assert "Pyroxene" in text and '"quantity":40' in text and "trace-01.png" in text
    assert "forged-line" in text and "\\x0a" in text


def test_journal_and_actions_have_real_times_context_and_safe_game_detail(tmp_path):
    state = tmp_path / "state"
    handler = daily_log.configure_daily_logging(state)
    run = tmp_path / "runs" / "spend_ap-20260924T120000-abcd1234"
    try:
        journal = Journal(run, lambda: 14, 10)
        journal.record(
            "intent", task="spend_ap", operation="tap", target=(100, 200), source="home"
        )
        logging.getLogger("ba_automator.test").info("waiting for the sweep receipt")
        action = record_action(
            SimpleNamespace(state_dir=state),
            "loot_received",
            "Items identified",
            task="spend_ap",
            items=[{"name": "Pyroxene", "quantity": 40}],
            evidence=str(run / "receipt.png"),
        )
        journal.record("finished", status="success")
        journal.close()
        logging.getLogger("ba_automator.test").info("next queue visit")
    finally:
        daily_log.close_daily_logging(handler)
    text = daily_log.read_day(state, daily_log.today())
    assert 'source="home"' in text
    assert f"[spend_ap/{run.name}] diagnostic | waiting" in text
    assert "[daemon/-] diagnostic | next queue" in text
    assert f'[record=action:{action["id"]}]' in text
    entries = [
        json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()
    ]
    assert all(
        datetime.fromisoformat(entry["time"]).utcoffset() == timedelta(0)
        for entry in entries
    )
    assert len({entry["event_id"] for entry in entries}) == 2
    assert daily_log.backfill_history(state, tmp_path / "runs") == 0


def test_journal_without_configured_handler_is_local_only_and_explicit_sink_works(
    tmp_path,
):
    journal = Journal(tmp_path / "run", lambda: 0, 0)
    journal.record("state", state="home")
    journal.close()
    assert not (tmp_path / "logs").exists()
    journal = Journal(tmp_path / "run2", lambda: 0, 0, state_dir=tmp_path)
    journal.record("finished", status="failed", detail="Lost foreground")
    journal.close()
    assert "ERROR" in daily_log.read_day(tmp_path, daily_log.today())


def test_backfill_estimates_old_journals_sorts_and_is_idempotent(tmp_path):
    when = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    day = when.astimezone().date().isoformat()
    state = tmp_path / "state"
    state.mkdir()
    (state / "important-actions.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "action1",
                        "time": (when + timedelta(seconds=4)).isoformat(),
                        "task": "cafe",
                        "action": "earnings_collected",
                        "detail": "Received earnings",
                        "credits": 50,
                    }
                ),
                "malformed",
                json.dumps({"time": "broken", "action": "bad time"}),
            ]
        )
        + "\n"
    )
    run = (
        tmp_path / "runs" / "dashboard-job1" / "runs" / "cafe-20260924T120000-a1b2c3d4"
    )
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event": "started", "elapsed": 0, "task": "cafe"}),
                json.dumps(
                    {
                        "event": "intent",
                        "elapsed": 3.5,
                        "operation": "tap",
                        "source": "home",
                        "target": [10, 20],
                    }
                ),
                json.dumps(
                    {
                        "event": "finished",
                        "elapsed": 8,
                        "status": "failed",
                        "reason": "Could not verify receipt",
                    }
                ),
                json.dumps({"event": "raw", "elapsed": "invalid"}),
            ]
        )
        + "\n"
    )
    daily_log.append_event(
        state, "already_live", timestamp=when + timedelta(seconds=12)
    )
    assert daily_log.backfill_history(state, tmp_path / "runs", day=day) == 4
    assert daily_log.backfill_history(state, tmp_path / "runs", day=day) == 0
    text = daily_log.read_day(state, day)
    assert text.count("timestamp_estimated=true") == 3
    assert (
        text.index("started")
        < text.index("intent")
        < text.index("earnings_collected")
        < text.index("finished")
        < text.index("already_live")
    )
    assert "ERROR" in text
    assert 'source="home"' in text
    assert "malformed" not in text and "bad time" not in text
    daily_log.append_event(
        state, "after_import", timestamp=when + timedelta(seconds=14)
    )
    assert daily_log.read_day(state, day).count("after_import") == 1


def test_backfill_handles_elapsed_events_crossing_midnight(tmp_path, monkeypatch):
    # UTC is forced at the conversion boundary to make this portable on Windows.
    monkeypatch.setattr(
        daily_log,
        "_moment",
        lambda value=None: (
            datetime.fromisoformat(value) if isinstance(value, str) else value
        ),
    )
    state = tmp_path / "state"
    run = tmp_path / "runs" / "cafe-20260924T235958-a1b2c3d4"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(
        json.dumps({"event": "before", "elapsed": 0})
        + "\n"
        + json.dumps({"event": "after", "elapsed": 5})
        + "\n"
    )
    assert daily_log.backfill_history(state, tmp_path / "runs", day="2026-09-25") == 1
    text = daily_log.read_day(state, "2026-09-25")
    assert "after" in text and "before" not in text


def test_popup_identity_does_not_collapse_separate_journal_events(tmp_path):
    run = tmp_path / "runs" / "restart-20260924T120000-aabbccdd"
    journal = Journal(run, lambda: 0, 0, state_dir=tmp_path)
    journal.record("popup_dismissal", id="same-popup", result="unchanged")
    journal.record("popup_dismissal", id="same-popup", result="changed")
    journal.close()
    records = [
        json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()
    ]
    assert records[0]["id"] == records[1]["id"] == "same-popup"
    assert records[0]["event_id"] != records[1]["event_id"]
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs") == 0
    text = daily_log.read_day(tmp_path, daily_log.today())
    assert text.count("popup_dismissal") == 2


def test_old_popup_identity_is_not_used_as_migration_identity(tmp_path):
    when = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    day = when.astimezone().date().isoformat()
    run = tmp_path / "runs" / "restart-20260924T120000-aabbccdd"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "event": "popup_dismissal",
                    "id": "same-popup",
                    "elapsed": index,
                    "result": result,
                }
            )
            for index, result in enumerate(["unchanged", "changed"])
        )
        + "\n"
    )
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 2
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 0
    assert daily_log.read_day(tmp_path, day).count("popup_dismissal") == 2


def test_diagnostic_write_failure_does_not_recurse(tmp_path, monkeypatch):
    handler = daily_log.configure_daily_logging(tmp_path)
    handled = []
    monkeypatch.setattr(handler, "handleError", handled.append)
    monkeypatch.setattr(
        daily_log,
        "append_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        logging.getLogger("ba_automator.test").error("Could not save state")
    finally:
        daily_log.close_daily_logging(handler)
    assert len(handled) == 1


def test_unexpected_diagnostic_includes_safe_stack_without_source_or_secrets(tmp_path):
    handler = daily_log.configure_daily_logging(tmp_path)
    try:
        try:
            raise ValueError("password=hidden user@private.example")
        except ValueError:
            logging.getLogger("ba_automator.test").exception("Unexpected task failure")
    finally:
        daily_log.close_daily_logging(handler)
    text = daily_log.read_day(tmp_path, daily_log.today())
    assert "test_daily_log.py" in text and '"line":' in text
    assert "hidden" not in text and "user@private.example" not in text
    assert "raise ValueError" not in text


def test_cli_failure_is_recorded_before_exit(tmp_path, monkeypatch):
    from ba_automator import cli, server

    monkeypatch.setattr(
        cli.Config, "from_file", lambda path: SimpleNamespace(state_dir=tmp_path)
    )

    def failed_serve(*args, **kwargs):
        raise RuntimeError("Could not reach home")

    monkeypatch.setattr(server, "serve", failed_serve)
    assert cli.main(["serve"]) == 1
    text = daily_log.read_day(tmp_path, daily_log.today())
    assert "command_started" in text
    assert "Command serve failed: Could not reach home" in text
    assert 'command_finished | status="failed"' in text
    assert not any(
        isinstance(handler, daily_log.DailyLogHandler)
        for handler in logging.getLogger().handlers
    )


def test_long_numeric_substrings_in_opaque_ids_are_preserved(tmp_path):
    identifier = "abc1234567890123456789def"
    daily_log.append_event(
        tmp_path,
        "job_started",
        run=identifier,
        job_id=identifier,
        record_id=f"journal:{identifier}",
        detail="Card 4242 4242 4242 4242 needs attention",
    )
    text = daily_log.read_day(tmp_path, daily_log.today())
    assert text.count(identifier) == 3
    assert "4242" not in text
    assert "[redacted number]" in text


@pytest.mark.parametrize(
    "invalid",
    [123, True, {"time": "private"}, ["2026-09-24"], "broken", "2026-09-24T12:00:00"],
)
def test_backfill_skips_malformed_timestamps_without_losing_neighbors(
    tmp_path, invalid
):
    when = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    day = when.astimezone().date().isoformat()
    state = tmp_path / "state"
    state.mkdir()
    (state / "important-actions.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {"id": "before", "time": when.isoformat(), "action": "before"},
                {"id": "broken", "time": invalid, "action": "should_not_import"},
                {
                    "id": "after",
                    "time": (when + timedelta(seconds=3)).isoformat(),
                    "action": "after",
                },
            ]
        )
        + "\n"
    )
    run = tmp_path / "runs" / "cafe-20260924T120000-aabbccdd"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {"event": "started", "time": when.isoformat(), "elapsed": 0},
                {"event": "should_not_import", "time": invalid, "elapsed": 1},
                {
                    "event": "finished",
                    "time": (when + timedelta(seconds=3)).isoformat(),
                    "elapsed": 3,
                },
            ]
        )
        + "\n"
    )
    assert daily_log.backfill_history(state, tmp_path / "runs", day=day) == 4
    text = daily_log.read_day(state, day)
    assert (
        "before" in text
        and "after" in text
        and "started" in text
        and "finished" in text
    )
    assert "should_not_import" not in text
    assert daily_log.backfill_history(state, tmp_path / "runs", day=day) == 0


@pytest.mark.parametrize(
    "invalid", ["invalid", None, {}, [], float("nan"), float("inf")]
)
def test_backfill_skips_invalid_elapsed_without_losing_neighbors(tmp_path, invalid):
    when = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    day = when.astimezone().date().isoformat()
    run = tmp_path / "runs" / "cafe-20260924T120000-aabbccdd"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {"event": "started", "elapsed": 0},
                {"event": "should_not_import", "elapsed": invalid},
                {"event": "finished", "elapsed": 3},
            ]
        )
        + "\n"
    )
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 2
    text = daily_log.read_day(tmp_path, day)
    assert "started" in text and "finished" in text and "should_not_import" not in text


def test_backfill_waits_for_final_jsonl_newline(tmp_path):
    when = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    day = when.astimezone().date().isoformat()
    run = tmp_path / "runs" / "cafe-20260924T120000-aabbccdd"
    run.mkdir(parents=True)
    path = run / "events.jsonl"
    path.write_text(json.dumps({"event": "started", "elapsed": 0}))
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 0
    with path.open("a") as stream:
        stream.write("\n")
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 1
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 0
    assert daily_log.read_day(tmp_path, day).count("started") == 1


def test_history_merge_sorts_absolute_instants_through_dst_fallback(tmp_path):
    # Existing log lines retain their original local offsets. They must sort by
    # actual chronology when an older event is later imported on that date.
    directory = tmp_path / "logs"
    directory.mkdir()
    (directory / "2026-11-01.log").write_text(
        "2026-11-01T01:59:00.000-07:00 INFO [daemon/-] before_fallback\n"
        "2026-11-01T01:00:00.000-08:00 INFO [daemon/-] after_fallback\n"
    )
    when = datetime(2026, 11, 1, 20, tzinfo=timezone.utc)
    # Choose a UTC time whose local date is Nov 1 on all supported test hosts.
    day = when.astimezone().date().isoformat()
    if day != "2026-11-01":
        pytest.skip("Host timezone shifts the test fixture to another calendar date")
    (tmp_path / "important-actions.jsonl").write_text(
        json.dumps({"id": "later", "time": when.isoformat(), "action": "later"}) + "\n"
    )
    assert daily_log.backfill_history(tmp_path, tmp_path / "runs", day=day) == 1
    text = daily_log.read_day(tmp_path, day)
    assert (
        text.index("before_fallback")
        < text.index("after_fallback")
        < text.index("later")
    )


def test_read_waits_for_other_process_to_finish_utf8_append(tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    script = r"""
from pathlib import Path
import os
import sys
from ba_automator.daily_log import _directory, _locked, today
folder = _directory(Path(sys.argv[1]), create=True)
with _locked(folder):
    with (folder / (today() + '.log')).open('wb') as stream:
        stream.write(b'complete record: caf\xc3')
        stream.flush()
        print('ready', flush=True)
        sys.stdin.readline()
        stream.write(b'\xa9\n')
        stream.flush()
        os.fsync(stream.fileno())
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "ready"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(daily_log.read_day, tmp_path, daily_log.today())
        try:
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        finally:
            child.stdin.write("finish\n")
            child.stdin.flush()
        assert future.result(timeout=20) == "complete record: café\n"
    _, error = child.communicate(timeout=20)
    assert child.returncode == 0, error


def test_long_diagnostic_redaction_finishes_without_quadratic_email_scan():
    # A subprocess bounds this regression if a future regex again tries every
    # suffix. The guard is deliberately generous for CI startup; healthy work
    # takes milliseconds and does not depend on filesystem/fsync performance.
    script = """
from ba_automator.daily_log import _safe_text
long_word = 'X' * 100000
assert _safe_text(long_word) == long_word
invalid_domain = long_word + '@' + long_word
assert _safe_text(invalid_domain) == invalid_domain
invalid_suffix = long_word + '@' + long_word + '.12345'
assert _safe_text(invalid_suffix) == invalid_suffix
assert _safe_text(long_word + '@private.example') == '[redacted email]'
assert _safe_text('message: user+tag@private.example; password=hidden') == 'message: [redacted email]; password=[redacted]'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
