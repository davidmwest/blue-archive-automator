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


@pytest.mark.parametrize(
    "items,complete,reason",
    [
        ([], False, "missing_details"),
        ([{"name": None, "quantity": 2}], False, "unidentified_items"),
        ([{"name": "Keystone", "quantity": None}], True, "unidentified_items"),
        ([{"name": "Keystone", "quantity": True}], True, "unidentified_items"),
        ([{"name": "Keystone", "quantity": 2}], False, "unverified_coverage"),
        ([{"name": "Keystone", "quantity": 2}], True, None),
    ],
)
def test_review_reason_distinguishes_missing_items_from_unproven_coverage(
    config, items, complete, reason
):
    record_action(
        config,
        "loot_received",
        "Receipt observations",
        items=items,
        items_complete=complete,
    )
    data = loot.snapshot(config)
    row = data["receipts"][0]
    assert row["review_reason"] == reason
    assert row["unidentified"] == (reason is not None)
    assert data["review_counts"] == {
        key: int(key == reason)
        for key in ("missing_details", "unidentified_items", "unverified_coverage")
    }
    assert sum(data["review_counts"].values()) == data["unidentified_receipts"]


def test_review_counts_cover_full_history_and_clear_without_changing_totals(config):
    for _ in range(101):
        record_action(
            config,
            "loot_received",
            "Named old cards without proven coverage",
            items=[{"name": "Keystone", "quantity": 2}],
            items_complete=False,
        )
    record_action(config, "ap_spent", "Legacy receipt without item details")
    record_action(
        config,
        "loot_received",
        "Partially read card",
        items=[{"name": None, "quantity": 3}],
        items_complete=False,
    )
    record_action(config, "earnings_collected", "Verified earnings", ap=40, credits=100)
    data = loot.snapshot(config)
    assert len(data["receipts"]) == 100
    assert data["review_counts"] == {
        "missing_details": 1,
        "unidentified_items": 1,
        "unverified_coverage": 101,
    }
    assert data["unidentified_receipts"] == 103
    assert data["items"] == [
        {"name": "AP", "quantity": 40},
        {"name": "Credits", "quantity": 100},
        {"name": "Keystone", "quantity": 202},
    ]
    loot.clear(config)
    assert loot.snapshot(config)["review_counts"] == {
        "missing_details": 0,
        "unidentified_items": 0,
        "unverified_coverage": 0,
    }


def test_cafe_missing_currency_is_coverage_issue_not_unknown_item(config):
    record_action(config, "earnings_collected", "Only credits verified", credits=100)
    data = loot.snapshot(config)
    assert data["receipts"][0]["review_reason"] == "unverified_coverage"
    assert data["unresolved_items"] == []
    assert data["items"] == [{"name": "Credits", "quantity": 100}]


def test_sidecar_enrichment_deduplicates_legacy_action_and_clear(config):
    config.run_dir.mkdir()
    image = config.run_dir / "receipt.png"
    image.write_bytes(b"proof")
    sidecar = {
        "version": 1,
        "items": [{"name": "Heat Pack Blueprint", "quantity": 2}],
        "items_complete": True,
    }
    image.with_suffix(".loot.json").write_text(json.dumps(sidecar))
    record_action(
        config, "loot_received", "receipt", task="spend_ap", evidence=str(image)
    )
    record_action(
        config, "ap_spent", "postcondition", task="spend_ap", evidence=str(image)
    )
    assert loot.snapshot(config)["items"] == [
        {"name": "Heat Pack Blueprint", "quantity": 2}
    ]
    assert loot.snapshot(config)["receipt_count"] == 1
    assert loot.snapshot(config)["unidentified_receipts"] == 0
    loot.clear(config)
    record_action(
        config, "ap_spent", "late postcondition", task="spend_ap", evidence=str(image)
    )
    assert loot.snapshot(config)["receipt_count"] == 0


def test_priority_groups_and_icons_preserve_clear_history(config, tmp_path):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"game icon")
    record_action(
        config,
        "loot_received",
        "verified",
        items=[
            {"name": "Credits", "quantity": 20},
            {"name": "Pyroxene", "quantity": 10, "icon_id": identifier},
            {"name": "Hina's Eleph", "quantity": 1},
            {"name": "AP", "quantity": 40},
            {"name": "General Bag Blueprint", "quantity": 2},
        ],
        items_complete=True,
    )
    groups = loot.snapshot(config)["groups"]
    assert [g["id"] for g in groups] == [
        "premium",
        "students",
        "energy",
        "equipment",
        "credits",
    ]
    assert groups[0]["items"][0]["icon_url"] == f"/api/loot/icons/{identifier}"
    assert loot.icon_path(config, "../private.png") is None
    assert loot.icon_path(config, "x" * 64) is None
    secret = tmp_path / "secret"
    secret.write_bytes(b"secret")
    link = config.state_dir / "loot-icons" / ("f" * 64 + ".png")
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlink unavailable")
    assert loot.icon_path(config, "f" * 64) is None


def test_unknown_icons_group_without_inventing_names(config):
    for _ in range(2):
        record_action(
            config,
            "loot_received",
            "partially read",
            items=[{"name": None, "quantity": 2, "icon_id": "a" * 64}],
            items_complete=False,
        )
    data = loot.snapshot(config)
    assert data["items"] == []
    assert data["unresolved_items"][0]["quantity"] == 4
    assert data["unidentified_receipts"] == 2


def receipt(config, items, *, complete=False):
    config.run_dir.mkdir(parents=True, exist_ok=True)
    image = config.run_dir / "receipt.png"
    image.write_bytes(b"proof")
    image.with_suffix(".loot.json").write_text(
        json.dumps(
            {
                "version": 1,
                "items": items,
                "items_complete": complete,
            }
        )
    )
    return image


def test_partial_static_backfill_keeps_all_verified_legacy_pages(config):
    image = receipt(config, [{"name": "Keystone", "quantity": 1}])
    record_action(
        config,
        "task_rewards_received",
        "All pages verified",
        evidence=str(image),
        items=[
            {"name": "Keystone", "quantity": 1},
            {"name": "Pyroxene", "quantity": 20},
            {"name": "Normal Activity Report", "quantity": 3},
        ],
        items_complete=False,
    )
    data = loot.snapshot(config)
    assert data["items"] == [
        {"name": "Keystone", "quantity": 1},
        {"name": "Normal Activity Report", "quantity": 3},
        {"name": "Pyroxenes", "quantity": 20},
    ]
    assert data["unidentified_receipts"] == 1


def test_later_completion_preserves_mail_balance_fallback(config):
    image = receipt(config, [{"name": "AP", "quantity": 10}])
    record_action(
        config,
        "loot_received",
        "Early receipt",
        evidence=str(image),
        items_complete=False,
    )
    record_action(
        config,
        "mail_received",
        "Confirmed later",
        evidence=str(image),
        balance_gains={"credits": 1200, "pyroxenes": 20, "ap": 1},
    )
    data = loot.snapshot(config)
    assert data["receipt_count"] == 1
    assert data["items"] == [
        {"name": "AP", "quantity": 10},
        {"name": "Credits", "quantity": 1200},
        {"name": "Pyroxenes", "quantity": 20},
    ]


def test_sidecar_duplicate_named_cards_are_summed_once(config):
    image = receipt(
        config,
        [
            {"name": "Credit Points", "quantity": 100},
            {"name": "Credit Points", "quantity": 200},
        ],
        complete=True,
    )
    record_action(config, "loot_received", "Early receipt", evidence=str(image))
    record_action(
        config,
        "mail_received",
        "Completion",
        evidence=str(image),
        items=[{"name": "Credits", "quantity": 300}],
        balance_gains={"credits": 300},
    )
    assert loot.snapshot(config)["items"] == [{"name": "Credits", "quantity": 300}]


def test_complete_sidecar_supersedes_truncated_legacy_labels(config):
    image = receipt(
        config,
        [{"name": "Beginner Tech Notes (Gehenna)", "quantity": 2}],
        complete=True,
    )
    record_action(
        config,
        "task_rewards_received",
        "Legacy OCR",
        evidence=str(image),
        items=[{"name": "Beginner Tech", "quantity": 2}],
    )
    assert loot.snapshot(config)["items"] == [
        {"name": "Beginner Tech Notes (Gehenna)", "quantity": 2}
    ]


def test_unreadable_new_quantity_keeps_verified_legacy_amount(config):
    image = receipt(config, [{"name": "  Credit   Points ", "quantity": None}])
    record_action(
        config,
        "mail_received",
        "Verified",
        evidence=str(image),
        items=[{"name": "Credits", "quantity": 100}],
    )
    data = loot.snapshot(config)
    assert data["items"] == [{"name": "Credits", "quantity": 100}]
    assert data["unidentified_receipts"] == 1


def test_pruned_receipt_still_deduplicates_and_stays_cleared(config):
    image = receipt(config, [], complete=False)
    record_action(
        config,
        "loot_received",
        "Received",
        evidence=str(image),
        items=[{"name": "AP", "quantity": 10}],
        items_complete=True,
    )
    image.unlink()
    record_action(
        config,
        "mail_received",
        "Completion",
        evidence=str(image),
        items=[{"name": "AP", "quantity": 10}],
    )
    assert loot.snapshot(config)["items"] == [{"name": "AP", "quantity": 10}]
    loot.clear(config)
    record_action(
        config,
        "loot_received",
        "Enriched later",
        evidence=str(image),
        items=[{"name": "AP", "quantity": 10}],
        items_complete=True,
    )
    assert loot.snapshot(config)["receipt_count"] == 0


def test_duplicate_action_id_does_not_return_after_clear_without_receipt(config):
    record_action(config, "earnings_collected", "Verified", ap=10, credits=100)
    path = config.state_dir / "important-actions.jsonl"
    row = path.read_bytes()
    loot.clear(config)
    with path.open("ab") as stream:
        stream.write(row)
    assert loot.snapshot(config)["receipt_count"] == 0


@pytest.mark.parametrize("items", [7, "bad", {"name": "AP"}, [7], [None]])
def test_malformed_items_do_not_crash_snapshot(config, items):
    record_action(
        config, "loot_received", "Malformed", items=items, items_complete=True
    )
    data = loot.snapshot(config)
    assert data["items"] == []
    assert data["unidentified_receipts"] == 1


@pytest.mark.parametrize("icon_id", [[], {}, 7, "../../private"])
def test_malformed_unknown_icon_does_not_crash_or_merge_cards(config, icon_id):
    record_action(
        config,
        "loot_received",
        "Unreadable",
        items=[
            {"name": None, "quantity": 2, "icon_id": icon_id},
            {"name": None, "quantity": 3, "icon_id": icon_id},
        ],
        items_complete=False,
    )
    data = loot.snapshot(config)
    assert len(data["unresolved_items"]) == 2
    assert [item["quantity"] for item in data["unresolved_items"]] == [2, 3]


def test_owned_and_boolean_are_never_received_quantities(config):
    record_action(
        config,
        "loot_received",
        "Bad quantities",
        items=[
            {"name": "Heat Pack Blueprint", "quantity": None, "owned": 436},
            {"name": "AP", "quantity": True},
        ],
        items_complete=True,
    )
    data = loot.snapshot(config)
    assert data["items"] == []
    assert data["unidentified_receipts"] == 1
    assert all(item["quantity"] is None for item in data["unresolved_items"])


def test_sidecar_cannot_read_symlink_and_malformed_metadata_is_ignored(
    config, tmp_path
):
    image = receipt(config, [{"name": "Credits", "quantity": 9000}], complete=True)
    sidecar = image.with_suffix(".loot.json")
    record_action(
        config,
        "loot_received",
        "Verified",
        evidence=str(image),
        items=[{"name": "AP", "quantity": 10}],
        items_complete=True,
    )
    for content in ("{invalid", "[]", '{"version":true,"items":[]}'):
        sidecar.write_text(content)
        assert loot.snapshot(config)["items"] == [{"name": "AP", "quantity": 10}]
    sidecar.unlink()
    secret = tmp_path / "outside.json"
    secret.write_text(
        json.dumps({"version": 1, "items": [{"name": "Credits", "quantity": 9000}]})
    )
    try:
        sidecar.symlink_to(secret)
    except OSError:
        pytest.skip("symlink privileges unavailable")
    assert loot.snapshot(config)["items"] == [{"name": "AP", "quantity": 10}]


def test_icon_directory_symlink_cannot_escape_state_root(config, tmp_path):
    outside = tmp_path / "private-icons"
    outside.mkdir()
    (outside / ("a" * 64 + ".png")).write_bytes(b"private")
    config.state_dir.mkdir()
    try:
        (config.state_dir / "loot-icons").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privileges unavailable")
    assert loot.icon_path(config, "a" * 64) is None


def test_named_unknown_quantity_is_visible_and_icon_whitespace_is_normalized(config):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"game icon")
    record_action(
        config,
        "loot_received",
        "Verified",
        items=[
            {"name": "  Credit    Points ", "quantity": 10, "icon_id": identifier},
            {"name": "Heat Pack Blueprint", "quantity": None},
        ],
        items_complete=False,
    )
    data = loot.snapshot(config)
    assert data["groups"][0]["items"][0]["icon_url"] == f"/api/loot/icons/{identifier}"
    assert data["unresolved_items"][0]["name"] == "Heat Pack Blueprint"
    assert data["unresolved_items"][0]["quantity"] is None


def test_partial_viewport_does_not_reduce_previously_verified_total(config):
    image = receipt(config, [{"name": "Credit Points", "quantity": 100}])
    record_action(
        config,
        "task_rewards_received",
        "Verified all pages",
        evidence=str(image),
        items=[{"name": "Credits", "quantity": 300}],
        items_complete=False,
    )
    data = loot.snapshot(config)
    assert data["items"] == [{"name": "Credits", "quantity": 300}]
    assert data["unidentified_receipts"] == 1


def test_shop_currency_priority_and_only_verified_artifact_names(config):
    record_action(
        config,
        "loot_received",
        "Verified",
        items=[
            {"name": "Tactical Challenge Coin", "quantity": 9},
            {"name": "Expert Permit", "quantity": 120},
            {"name": "Crystal Haniwa Fragment", "quantity": 1},
            {"name": "Unknown Event Fragment", "quantity": 2},
            {"name": "AP", "quantity": 10},
        ],
        items_complete=True,
    )
    groups = loot.snapshot(config)["groups"]
    assert [group["id"] for group in groups] == ["energy", "shop", "growth", "other"]
    assert [item["name"] for item in groups[1]["items"]] == [
        "Expert Permit",
        "Tactical Challenge Coin",
    ]
    assert groups[2]["items"][0]["name"] == "Crystal Haniwa Fragment"
    assert groups[3]["items"][0]["name"] == "Unknown Event Fragment"


def test_exact_catalog_name_enriches_old_receipt_without_changing_history(config):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"exact crop")
    image = receipt(config, [{"name": None, "quantity": 2, "icon_id": identifier}])
    record_action(config, "ap_spent", "Legacy receipt", evidence=str(image))
    history = config.state_dir / "important-actions.jsonl"
    before = history.read_bytes()
    sidecar = image.with_suffix(".loot.json")
    sidecar_before = sidecar.read_bytes()
    assert loot.snapshot(config)["items"] == []
    catalog = config.state_dir / "loot-icons" / (identifier + ".json")
    catalog.write_text(json.dumps({"name": "Heat Pack Blueprint"}))
    data = loot.snapshot(config)
    assert data["items"] == [{"name": "Heat Pack Blueprint", "quantity": 2}]
    assert data["unresolved_items"] == []
    # A name match does not prove an old multi-page receipt is complete.
    assert data["unidentified_receipts"] == 1
    assert history.read_bytes() == before and sidecar.read_bytes() == sidecar_before
    loot.clear(config)
    catalog.write_text(json.dumps({"name": "Other Learned Name"}))
    assert loot.snapshot(config)["items"] == []


def test_catalog_lookup_precedes_legacy_fallback_to_avoid_double_counting(config):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"exact crop")
    (config.state_dir / "loot-icons" / (identifier + ".json")).write_text(
        json.dumps({"name": "Credit Points"})
    )
    image = receipt(config, [{"name": None, "quantity": 100, "icon_id": identifier}])
    record_action(
        config,
        "mail_received",
        "Verified currency",
        evidence=str(image),
        items=[{"name": "Credits", "quantity": 100}],
    )
    data = loot.snapshot(config)
    assert data["items"] == [{"name": "Credits", "quantity": 100}]
    assert data["unresolved_items"] == []


@pytest.mark.parametrize(
    "name", [None, [], 7, "", "x100", "Owned: 436", "123", "A" * 161, "Name\nInjected"]
)
def test_invalid_catalog_names_leave_items_unidentified(config, name):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"exact crop")
    (config.state_dir / "loot-icons" / (identifier + ".json")).write_text(
        json.dumps({"name": name})
    )
    record_action(
        config,
        "loot_received",
        "Unreadable",
        items=[
            {"name": None, "quantity": 1, "icon_id": identifier},
        ],
        items_complete=False,
    )
    assert loot.snapshot(config)["items"] == []
    assert len(loot.snapshot(config)["unresolved_items"]) == 1


def test_catalog_cannot_override_verified_name_or_use_changed_icon_bytes(config):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"exact crop")
    directory = config.state_dir / "loot-icons"
    (directory / (identifier + ".json")).write_text(json.dumps({"name": "Wrong Name"}))
    record_action(
        config,
        "loot_received",
        "Verified",
        items=[
            {"name": "Heat Pack Blueprint", "quantity": 1, "icon_id": identifier},
        ],
        items_complete=True,
    )
    assert loot.snapshot(config)["items"] == [
        {"name": "Heat Pack Blueprint", "quantity": 1}
    ]
    (directory / (identifier + ".png")).write_bytes(b"changed crop")
    record_action(
        config,
        "loot_received",
        "Unknown",
        items=[
            {"name": None, "quantity": 2, "icon_id": identifier},
        ],
        items_complete=False,
    )
    assert loot.snapshot(config)["items"] == [
        {"name": "Heat Pack Blueprint", "quantity": 1}
    ]


def test_catalog_symlink_is_not_read(config, tmp_path):
    from ba_automator.loot_receipts import save_icon

    identifier = save_icon(config, b"exact crop")
    secret = tmp_path / "private.json"
    secret.write_text(json.dumps({"name": "Private data"}))
    try:
        (config.state_dir / "loot-icons" / (identifier + ".json")).symlink_to(secret)
    except OSError:
        pytest.skip("symlink privileges unavailable")
    record_action(
        config,
        "loot_received",
        "Unreadable",
        items=[
            {"name": None, "quantity": 1, "icon_id": identifier},
        ],
        items_complete=False,
    )
    assert loot.snapshot(config)["items"] == []
