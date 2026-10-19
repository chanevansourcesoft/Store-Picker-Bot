"""
Tests for load_stations() in robot_server/jetson_server.py

Uses temporary yaml files, so a real stations.yaml is never touched.
Tests that read yaml need PyYAML (pip install pyyaml); without it they are skipped.

Run:
    python -m robot_server.test_stations
"""

import tempfile
from pathlib import Path

from robot_server import jetson_server as js

EXAMPLE_FILE = Path(__file__).resolve().parent / "stations.example.yaml"

GOOD_YAML = """
stations:
  home:     {x: 0, y: 0, theta: 0}
  shelf_A:  {x: 2.0, y: 1.0, theta: 1.57}
  shelf_B:  {x: 2.0, y: 2.0, theta: 1.57}
  shelf_C:  {x: 2.0, y: 3.0}
  customer: {x: 0.0, y: 3.0, theta: 3.14}
"""


def load_text(text):
    """Write text to a temp yaml file and load it."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "stations.yaml"
        path.write_text(text)
        return js.load_stations(path)


def has_yaml():
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def test_missing_file_uses_fallback():
    stations, source = js.load_stations(Path(tempfile.gettempdir()) / "no_such_stations.yaml")
    assert stations == js.STATION_MAP
    assert "not found" in source
    print("PASS: test_missing_file_uses_fallback")


def test_no_pyyaml_uses_fallback():
    """Without PyYAML the server must still start, with STATION_MAP."""
    stations, source = load_text(GOOD_YAML)
    assert stations == js.STATION_MAP
    assert "PyYAML" in source
    print("PASS: test_no_pyyaml_uses_fallback")


def test_example_file_matches_station_map():
    """Copying the example file must not change anything."""
    stations, source = js.load_stations(EXAMPLE_FILE)
    assert source == EXAMPLE_FILE.name, source
    assert stations == js.STATION_MAP, stations
    print("PASS: test_example_file_matches_station_map")


def test_good_file_is_used():
    stations, source = load_text(GOOD_YAML)
    assert source == "stations.yaml", source
    assert stations["shelf_A"] == {"x": 2.0, "y": 1.0, "theta": 1.57}
    assert stations["home"] == {"x": 0.0, "y": 0.0, "theta": 0.0}  # ints become floats
    assert stations["shelf_C"]["theta"] == 0.0  # theta is optional
    print("PASS: test_good_file_is_used")


def test_unknown_station_is_ignored():
    stations, source = load_text(GOOD_YAML + "  back_door: {x: 5, y: 5}\n")
    assert source == "stations.yaml"
    assert "back_door" not in stations
    print("PASS: test_unknown_station_is_ignored")


def test_bad_files_use_fallback():
    """Any problem -> the whole STATION_MAP (never a mix)."""
    bad_files = {
        "missing station": GOOD_YAML.replace("  customer: {x: 0.0, y: 3.0, theta: 3.14}\n", ""),
        "text instead of number": GOOD_YAML.replace("x: 2.0, y: 1.0", "x: two, y: 1.0"),
        "missing y": GOOD_YAML.replace("{x: 2.0, y: 2.0, theta: 1.57}", "{x: 2.0, theta: 1.57}"),
        "no stations section": "home: {x: 0, y: 0}\n",
        "broken yaml": "stations: [home: {x: 0\n",
        "empty file": "",
    }
    for name, text in bad_files.items():
        stations, source = load_text(text)
        assert stations == js.STATION_MAP, name
        assert "fallback" in source, (name, source)
    print("PASS: test_bad_files_use_fallback")


def test_map_endpoint_uses_loaded_stations():
    assert js.get_map() == {"stations": js.STATIONS}
    print("PASS: test_map_endpoint_uses_loaded_stations")


def run_all_tests():
    print("Running station tests...\n")

    test_missing_file_uses_fallback()
    test_map_endpoint_uses_loaded_stations()
    if has_yaml():
        test_example_file_matches_station_map()
        test_good_file_is_used()
        test_unknown_station_is_ignored()
        test_bad_files_use_fallback()
    else:
        test_no_pyyaml_uses_fallback()
        print("SKIP: yaml tests (PyYAML not installed, run: pip install pyyaml)")

    print("\nAll station tests passed!")


if __name__ == "__main__":
    run_all_tests()
