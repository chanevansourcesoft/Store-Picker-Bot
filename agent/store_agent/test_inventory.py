"""
Tests for store_agent/inventory.py: the catalog, the policy contract and the live stock.

Uses a temporary stock file, so the real one is never touched.

Run:
    python -m store_agent.test_inventory
"""

import store_agent.testing_store  # noqa: F401  (first: the tests use the test store in agent/testdata)
import json
import tempfile
import threading
from pathlib import Path

from store_agent import inventory


def use_temp_stock():
    inventory.STOCK_FILE = Path(tempfile.mkdtemp()) / "stock.json"
    return inventory.STOCK_FILE


def test_catalog_matches_policy():
    """Exactly the items GR00T was trained on (the test store's instructions.json), at the same levels."""
    problems = inventory.check_catalog()
    assert problems == [], problems
    assert set(inventory.load_catalog()) == {"chips_packet", "mentos", "frooti", "hide_and_seek"}
    print("PASS: test_catalog_matches_policy")


def test_real_catalog_matches_policy():
    """The real store (store_agent/catalog.json): one rack (a Nav2 station) with the products on two
    shelf levels, with the same ids as policy/instructions.json. Checked against the real
    policy/instructions.json when it is there (it is git-ignored: on the robot only)."""
    catalog = json.loads((inventory.HERE / "catalog.json").read_text())["products"]
    assert {item: (info["shelf"], info["level"]) for item, info in catalog.items()} == {
        "ink_box": ("shelf_A", "upper_shelf"),
        "biscuit": ("shelf_A", "lower_shelf"),
    }, catalog
    policy_file = inventory.REPO_ROOT / "policy" / "instructions.json"
    if policy_file.exists():
        problems = inventory.check_catalog(catalog, policy_file)
        assert problems == [], problems
        print("PASS: test_real_catalog_matches_policy (checked against policy/instructions.json)")
    else:
        print("PASS: test_real_catalog_matches_policy (policy/instructions.json not here: ids and levels only)")


def test_check_catalog_finds_mismatches():
    policy = {"chips_packet": {"shelf": "upper_shelf"}, "mentos": {"shelf": "lower_shelf"}}
    catalog = {"chips_packet": {"shelf": "shelf_A", "level": "lower_shelf"},
               "soup_can": {"shelf": "shelf_A", "level": "upper_shelf"}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "instructions.json"
        path.write_text(json.dumps(policy))
        problems = inventory.check_catalog(catalog, path)
        # The policy "shelf" is a level: it is never compared with the station (shelf_A)
        good = {"chips_packet": {"shelf": "shelf_A", "level": "upper_shelf"},
                "mentos": {"shelf": "shelf_A", "level": "lower_shelf"}}
        assert inventory.check_catalog(good, path) == []
        no_station = {"chips_packet": {"level": "upper_shelf"}, "mentos": good["mentos"]}
        assert inventory.check_catalog(no_station, path) == ["chips_packet: no station (rack) in the catalog"]
    assert problems == [
        "mentos has a GR00T instruction but is not in the catalog",
        "soup_can is in the catalog but has no GR00T instruction",
        "chips_packet: catalog level lower_shelf != policy shelf upper_shelf",
    ], problems
    print("PASS: test_check_catalog_finds_mismatches")


def test_station_of_item():
    assert inventory.station_of("frooti") == "shelf_B" and inventory.station_of("mentos") == "shelf_A"
    assert inventory.station_of("pizza") is None
    print("PASS: test_station_of_item")


def test_display_names_come_from_catalog():
    assert inventory.product_name("hide_and_seek") == "Hide & Seek"
    assert inventory.product_name("unknown_thing") == "Unknown Thing"
    print("PASS: test_display_names_come_from_catalog")


def test_stock_starts_from_catalog_and_is_saved():
    path = use_temp_stock()
    assert inventory.ensure_stock() is True and path.exists()
    assert inventory.ensure_stock() is False
    initial = {item: info["initial_stock"] for item, info in inventory.load_catalog().items()}
    assert inventory.get_stock() == initial

    assert inventory.take_stock("frooti", 2)
    assert not inventory.take_stock("frooti", 1000), "never below zero"
    assert not inventory.take_stock("pizza", 1)
    assert inventory.add_stock("mentos", 5)
    assert not inventory.add_stock("pizza", 5)
    saved = json.loads(path.read_text())
    assert saved["frooti"] == initial["frooti"] - 2 and saved["mentos"] == initial["mentos"] + 5, saved

    full = inventory.load_inventory()
    assert full["frooti"]["stock"] == initial["frooti"] - 2
    assert full["frooti"]["shelf"] == "shelf_B" and full["frooti"]["name"] == "Frooti"
    print("PASS: test_stock_starts_from_catalog_and_is_saved")


def test_catalog_file_is_never_written():
    use_temp_stock()
    before = inventory.CATALOG_FILE.read_text()
    inventory.take_stock("chips_packet", 1)
    inventory.add_stock("chips_packet", 3)
    assert inventory.CATALOG_FILE.read_text() == before
    print("PASS: test_catalog_file_is_never_written")


def test_parallel_takes_do_not_lose_updates():
    use_temp_stock()
    start = inventory.get_stock()["mentos"]
    threads = [threading.Thread(target=inventory.take_stock, args=("mentos", 1)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert inventory.get_stock()["mentos"] == start - 10
    print("PASS: test_parallel_takes_do_not_lose_updates")


def test_reserve_is_all_or_nothing():
    use_temp_stock()
    start = inventory.get_stock()
    ok, problems = inventory.reserve([{"item": "frooti", "qty": 2}, {"item": "mentos", "qty": 999}])
    assert not ok and problems == [f"Not enough 'mentos': need 999, have {start['mentos']}."], problems
    assert inventory.get_stock() == start, "nothing reserved when one item is short"
    assert inventory.reserve([{"item": "frooti", "qty": 2}, {"item": "frooti", "qty": 1}]) == (True, [])
    assert inventory.get_stock()["frooti"] == start["frooti"] - 3
    inventory.release({"frooti": 2, "pizza": 5})
    assert inventory.get_stock()["frooti"] == start["frooti"] - 1
    print("PASS: test_reserve_is_all_or_nothing")


def run_all_tests():
    print("Running inventory tests...\n")
    test_catalog_matches_policy()
    test_real_catalog_matches_policy()
    test_check_catalog_finds_mismatches()
    test_station_of_item()
    test_display_names_come_from_catalog()
    test_stock_starts_from_catalog_and_is_saved()
    test_catalog_file_is_never_written()
    test_parallel_takes_do_not_lose_updates()
    test_reserve_is_all_or_nothing()
    print("\nAll inventory tests passed!")


if __name__ == "__main__":
    run_all_tests()
