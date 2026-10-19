"""
Tests for store_agent/inventory.py: the catalog, the policy contract and the live stock.

Uses a temporary stock file, so the real one is never touched.

Run:
    python -m store_agent.test_inventory
"""

import json
import tempfile
import threading
from pathlib import Path

from store_agent import inventory


def use_temp_stock():
    inventory.STOCK_FILE = Path(tempfile.mkdtemp()) / "stock.json"
    return inventory.STOCK_FILE


def test_catalog_matches_policy():
    """Exactly the items GR00T was trained on (policy/instructions.json), on the same shelves."""
    problems = inventory.check_catalog()
    assert problems == [], problems
    assert set(inventory.load_catalog()) == {"chips_packet", "mentos", "frooti", "hide_and_seek"}
    print("PASS: test_catalog_matches_policy")


def test_check_catalog_finds_mismatches():
    policy = {"chips_packet": {"shelf": "shelf_A"}, "mentos": {"shelf": "shelf_A"}}
    catalog = {"chips_packet": {"shelf": "shelf_B"}, "soup_can": {"shelf": "shelf_A"}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "instructions.json"
        path.write_text(json.dumps(policy))
        problems = inventory.check_catalog(catalog, path)
    assert problems == [
        "mentos has a GR00T instruction but is not in the catalog",
        "soup_can is in the catalog but has no GR00T instruction",
        "chips_packet: catalog shelf shelf_B != policy shelf shelf_A",
    ], problems
    print("PASS: test_check_catalog_finds_mismatches")


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


def run_all_tests():
    print("Running inventory tests...\n")
    test_catalog_matches_policy()
    test_check_catalog_finds_mismatches()
    test_display_names_come_from_catalog()
    test_stock_starts_from_catalog_and_is_saved()
    test_catalog_file_is_never_written()
    test_parallel_takes_do_not_lose_updates()
    print("\nAll inventory tests passed!")


if __name__ == "__main__":
    run_all_tests()
