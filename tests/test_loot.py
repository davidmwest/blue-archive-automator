from pathlib import Path
import json
import pytest
from ba_automator.config import Config
from ba_automator.actions import record_action
from ba_automator import loot


@pytest.fixture
def config(tmp_path):
    return Config(
        serial="127.0.0.1:5695",
        package="com.nexon.bluearchive",
        run_dir=tmp_path / "runs",
        state_dir=tmp_path / "state",
    )


def test_empty_and_confirmed_rewards_only(config):
    assert loot.snapshot(config)["items"] == []
    record_action(config, "earnings_claim_attempted", "attempt")
    record_action(config, "crafts_started", "cost, not loot", task="crafting", count=3)
    record_action(config, "earnings_collected", "verified", ap=40, credits=1000)
    record_action(
        config,
        "mail_received",
        "verified",
        task="mail",
        items=[{"name": "Pyroxene", "quantity": 100}],
        balance_gains={"pyroxenes": 100, "ap": 1},
    )
    data = loot.snapshot(config)
    assert data["items"] == [
        {"name": "AP", "quantity": 40},
        {"name": "Credits", "quantity": 1000},
        {"name": "Pyroxenes", "quantity": 100},
    ]
    assert data["receipt_count"] == 2 and data["unidentified_receipts"] == 0


def test_clear_persists_and_preserves_history_and_receipt(config):
    config.run_dir.mkdir(parents=True)
    image = config.run_dir / "receipt.png"
    image.write_bytes(b"proof")
    event = record_action(
        config, "tickets_spent", "verified", task="bounties", evidence=str(image)
    )
    path = config.state_dir / "important-actions.jsonl"
    before = path.read_bytes()
    assert loot.snapshot(config)["unidentified_receipts"] == 1
    assert loot.image_path(config, event["id"]) == image
    loot.clear(config)
    assert path.read_bytes() == before and image.read_bytes() == b"proof"
    assert loot.snapshot(config)["receipt_count"] == 0
    record_action(config, "earnings_collected", "new", ap=20, credits=300)
    data = loot.snapshot(config)
    assert data["cleared_at"] and data["receipt_count"] == 1
    assert data["items"] == [
        {"name": "AP", "quantity": 20},
        {"name": "Credits", "quantity": 300},
    ]
    assert loot.image_path(config, event["id"]) == image


def test_clear_does_not_swallow_an_append_in_progress(config):
    record_action(config, "earnings_collected", "old", ap=10, credits=2)
    path = config.state_dir / "important-actions.jsonl"
    event = json.dumps(
        {
            "id": "f" * 32,
            "action": "mail_received",
            "items": [{"name": "Keystone", "quantity": 3}],
        }
    ).encode()
    with path.open("ab") as stream:
        stream.write(event[:20])
    loot.clear(config)
    with path.open("ab") as stream:
        stream.write(event[20:] + b"\n")
    assert loot.snapshot(config)["items"] == [{"name": "Keystone", "quantity": 3}]


def test_duplicate_ids_do_not_double_count_and_malformed_rows_ignored(config):
    record_action(config, "earnings_collected", "verified", ap=10, credits=100)
    path = config.state_dir / "important-actions.jsonl"
    row = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(row + b"{broken}\n[]\n")
    assert loot.snapshot(config)["receipt_count"] == 1


def test_receipt_cannot_escape_run_root_or_follow_symlink(config, tmp_path):
    secret = tmp_path / "private.png"
    secret.write_bytes(b"private")
    event = record_action(config, "mail_received", "verified", evidence=str(secret))
    assert loot.image_path(config, event["id"]) is None
    config.run_dir.mkdir()
    link = config.run_dir / "link.png"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlink privileges unavailable")
    event = record_action(config, "mail_received", "verified", evidence=str(link))
    assert loot.image_path(config, event["id"]) is None


def test_totals_include_older_rows_beyond_receipt_display_limit(config):
    for _ in range(105):
        record_action(config, "earnings_collected", "verified", ap=1, credits=2)
    data = loot.snapshot(config)
    assert (
        data["receipt_count"] == 105
        and len(data["receipts"]) == 100
        and data["older_receipts"] == 5
    )
    assert data["items"] == [
        {"name": "AP", "quantity": 105},
        {"name": "Credits", "quantity": 210},
    ]


def test_zero_unknown_drops_not_invented(config):
    record_action(
        config, "crafts_collected", "receipt verified", task="crafting", count=3
    )
    record_action(
        config,
        "ap_spent",
        "receipt verified",
        task="spend_ap",
        rewards=["x1", "x8"],
        ap_spent=20,
    )
    data = loot.snapshot(config)
    assert (
        data["items"] == []
        and data["receipt_count"] == 2
        and data["unidentified_receipts"] == 2
    )
