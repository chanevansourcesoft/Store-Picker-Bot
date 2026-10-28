"""
Autopilot: an offline stand-in for Nemotron, used when NEBIUS_API_KEY is missing.

It looks like an OpenAI client (client.chat.completions.create), so the agent
loop does not change. Instead of a model it follows the same rules the system
prompt gives Nemotron:

    per unit: go to its shelf -> pick (one GR00T run: pick AND place in the basket).
              The pick tool itself retries a failed grasp (PICK_RETRIES); an item it reports
              as not_picked is skipped. Other failed picks (no arm motion) are tried again,
              up to MAX_TRIES.
    then: customer -> home -> DONE

It reads the truth from the OrderTracker (what is packed, where the robot is)
and the last tool result (did the pick / navigation work?).
The code guards still check every command, exactly as with Nemotron.

Example:
    client = Autopilot()
    bill, log = run_agent(order, client=client)   # run_agent attaches the tracker
"""

import json
from types import SimpleNamespace

MAX_TRIES = 3          # pick calls per unit that failed without an arm episode, navigation tries per station
MAX_REFUSALS = 5       # refused commands before we give up (should never happen)


def _call(name, args, index):
    """One tool call in the OpenAI response shape."""
    call = SimpleNamespace(
        id=f"autopilot_{index}",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )
    message = SimpleNamespace(tool_calls=[call], content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _done():
    message = SimpleNamespace(tool_calls=None, content="DONE")
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _last_result(messages):
    """The robot result of the last tool call (first line of the tool message), or None."""
    if not messages or messages[-1].get("role") != "tool":
        return None
    first_line = str(messages[-1].get("content", "")).split("\n", 1)[0]
    try:
        return json.loads(first_line)
    except json.JSONDecodeError:
        return None


class Autopilot:
    is_autopilot = True

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.tracker = None
        self.last = None            # (tool, args) we sent last
        self.calls = 0
        self.pick_tries = {}        # item -> failed tries for the current unit
        self.nav_tries = {}         # station -> failed tries
        self.skipped = set()        # items we gave up on
        self.refusals = 0
        self.delivered = False
        self.home = False

    def attach(self, tracker):
        """run_agent gives us the OrderTracker before the first call."""
        self.tracker = tracker

    # ---------- Learn from the last result ----------

    def _learn(self, result):
        if self.last is None or result is None:
            return
        tool, args = self.last
        ok = bool(result.get("success"))
        if result.get("refused"):
            self.refusals += 1
            return
        if tool == "navigate_to":
            station = args["station"]
            if ok:
                self.nav_tries[station] = 0
                self.delivered = self.delivered or station == "customer"
                self.home = station == "home" and self.delivered
            else:
                self.nav_tries[station] = self.nav_tries.get(station, 0) + 1
        elif tool == "pick_item":
            item = args["item"]
            if ok:
                self.pick_tries[item] = 0     # this unit is in the basket; the next unit starts again
            elif result.get("not_picked"):
                self.skipped.add(item)        # the tool already retried: reported, go on with the order
            else:
                self.pick_tries[item] = self.pick_tries.get(item, 0) + 1

    # ---------- Decide the next call ----------

    def _go(self, station):
        """Navigate, or None if this station failed too often."""
        if self.nav_tries.get(station, 0) >= MAX_TRIES:
            return None
        return ("navigate_to", {"station": station})

    def _next(self):
        t = self.tracker
        if t.holding:
            return None  # the robot reports an item in the gripper: stop, the operator checks the arm

        for entry in t.missing():
            item = entry["item"]
            if item in self.skipped or t.not_picked.get(item):
                continue
            if self.pick_tries.get(item, 0) >= MAX_TRIES:
                self.skipped.add(item)
                continue
            shelf = t.shelf_of(item)
            if t.station != shelf:
                step = self._go(shelf)
                if step is None:
                    self.skipped.add(item)
                    continue
                return step
            return ("pick_item", {"item": item})

        if not self.delivered:
            step = self._go("customer")
            if step is not None:
                return step
            self.delivered = True  # could not reach the customer: still go home
        if not self.home:
            return self._go("home")
        return None

    def create(self, messages, **kwargs):
        if self.tracker is None:
            raise RuntimeError("Autopilot needs an OrderTracker: call attach(tracker) first")
        self._learn(_last_result(messages))
        if self.refusals >= MAX_REFUSALS:
            return _done()
        step = self._next()
        if step is None:
            self.last = None
            return _done()
        self.last = step
        self.calls += 1
        return _call(step[0], step[1], self.calls)
