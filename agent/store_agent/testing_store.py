"""
Point the code at the fixed TEST STORE in agent/testdata, for tests. Import it FIRST in a test
module, before anything from store_agent, robot_server or the apps:

    import store_agent.testing_store  # noqa: F401  (must be the first import)

The test store has two racks (shelf_A: chips_packet, mentos; shelf_B: frooti, hide_and_seek)
with matching GR00T instructions, so tests do not depend on the real catalog (which changes
with what GR00T is trained on) or on policy/instructions.json (git-ignored, only on the robot).

The real catalog is still checked against the real policy file in test_inventory.
"""

import os
from pathlib import Path

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"
TEST_CATALOG = TESTDATA / "catalog.json"
TEST_INSTRUCTIONS = TESTDATA / "instructions.json"

# Forced, not setdefault: a developer's own settings must not change what the tests see.
os.environ["STORE_CATALOG_FILE"] = str(TEST_CATALOG)
os.environ["POLICY_INSTRUCTIONS_FILE"] = str(TEST_INSTRUCTIONS)
