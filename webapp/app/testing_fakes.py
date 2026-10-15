"""
Fakes for tests: a robot with scripted failures and a model that follows a script.

FakeRobot works like the mock robot, but failures happen on chosen attempts
(not random), so a test gives the same result every time.

ScriptedModel is a fake Nemotron client. It sends the given tool calls one by
one, no matter what the results say (like a model that lost track), then "DONE".
"""

import json
from types import SimpleNamespace


class FakeRobot:
    def __init__(self, inventory, nav_fail=(), pick_fail=(), scan_fail=()):
        """nav_fail / pick_fail / scan_fail: attempt numbers (1, 2, ...) that fail."""
        self.inventory = inventory
        self.nav_fail, self.pick_fail, self.scan_fail = set(nav_fail), set(pick_fail), set(scan_fail)
        self.nav_count = self.pick_count = self.scan_count = 0
        self.station = "home"
        self.holding = None
        self.commands = []  # every command that reached the robot: (name, args)
        self.errors = []    # every error message the robot answered

    def _fail(self, message):
        self.errors.append(message)
        return {"success": False, "message": message}

    def health(self):
        return {"status": "ok"}

    def status(self):
        return {"busy": False, "holding": self.holding, "station": self.station, "last_error": None}

    def navigate(self, station):
        self.commands.append(("navigate", station))
        if station == self.station:
            return {"success": True, "message": f"already at {station}"}
        self.nav_count += 1
        if self.nav_count in self.nav_fail:
            return self._fail("navigation failed, obstacle detected")
        self.station = station
        return {"success": True, "message": f"arrived at {station}"}

    def pick(self, item, shelf, instruction=None):
        self.commands.append(("pick", item))
        if self.holding:
            return self._fail(f"already holding {self.holding}")
        if self.station != shelf:
            return self._fail(f"not at a shelf, currently at {self.station}")
        self.pick_count += 1
        if self.pick_count in self.pick_fail:
            return self._fail("grasp failed")
        self.holding = item
        return {"success": True, "message": "picked"}

    def scan(self):
        self.commands.append(("scan", None))
        if not self.holding:
            return self._fail("not holding anything")
        self.scan_count += 1
        if self.scan_count in self.scan_fail:
            return {"success": False, "barcode": None, "message": "barcode not readable"}
        return {"success": True, "barcode": self.inventory[self.holding]["barcode"]}

    def place(self, target):
        self.commands.append(("place", target))
        if not self.holding:
            return self._fail("not holding anything")
        item = self.holding
        if target == "return" and self.station != self.inventory[item]["shelf"]:
            return self._fail(f"cannot return {item} here")
        self.holding = None
        if target == "basket":
            return {"success": True, "message": f"placed {item} in basket"}
        return {"success": True, "message": f"returned {item} to shelf"}

    def picks_sent(self, item):
        return sum(1 for name, arg in self.commands if name == "pick" and arg == item)


class ScriptedModel:
    """Fake OpenAI client: client.chat.completions.create(...) follows the script."""

    def __init__(self, calls):
        """calls: list of (tool_name, args), or None where the model says DONE."""
        self.calls = list(calls)
        self.index = 0
        self.seen_messages = []  # every message list the model got
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.seen_messages.append([dict(m) for m in kwargs["messages"]])
        if self.index < len(self.calls) and self.calls[self.index] is None:
            self.index += 1  # None in the script = the model says DONE here
            message = SimpleNamespace(tool_calls=None, content="DONE")
        elif self.index < len(self.calls):
            name, args = self.calls[self.index]
            self.index += 1
            call = SimpleNamespace(
                id=f"call_{self.index}",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            message = SimpleNamespace(tool_calls=[call], content=None)
        else:
            message = SimpleNamespace(tool_calls=None, content="DONE")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def tool_messages(self):
        """Tool result texts the model saw (from the last request)."""
        last = self.seen_messages[-1] if self.seen_messages else []
        return [m["content"] for m in last if m.get("role") == "tool"]


# Shortcuts for scripts
def nav(station):
    return ("navigate_to", {"station": station})


def pick(item):
    return ("pick_item", {"item": item, "instruction": f"pick up the {item.replace('_', ' ')}"})


SCAN = ("scan_item", {})
BASKET = ("place_item", {"target": "basket"})
RETURN = ("place_item", {"target": "return"})
