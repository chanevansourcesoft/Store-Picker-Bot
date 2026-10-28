"""
Order tracker: the CODE keeps the truth about an order, not the model.

Per item it knows: ordered qty, packed qty (in the basket), extra qty
(put in the basket but not ordered) and not-picked qty (given up after the pick
retries: reported, the order goes on). It also knows where the robot is, and
what it holds (normally nothing: one pick_item = pick AND place in the basket).
If the gripper still holds something after a pick, needs_operator says why:
the planner stops and a person checks the arm.

It changes ONLY from real robot results (record() and sync_from_status()).
The tools ask it before a robot command (check_pick). If a command would be a
mistake, it is refused and the robot never gets it.

Example:
    tracker = OrderTracker([{"item": "chips_packet", "qty": 2}], inventory)
    tracker.check_pick("chips_packet")   # None = allowed, or "REFUSED: ..." text (also: wrong station)
    tracker.progress_line()              # "Progress: chips_packet 0/2 packed. Robot at: home. ..."
"""


def basket_item(entry):
    """Item a logged step put in the basket, else None (structured fields only).

    Contract now: a successful pick_item with result target "basket".
    Older logs: a successful place_item with target "basket" and an "item" field.
    """
    result = entry.get("result") or {}
    if not result.get("success") or result.get("refused"):
        return None
    tool = entry.get("tool")
    if tool == "pick_item" and result.get("target") == "basket":
        return result.get("item") or (entry.get("input") or {}).get("item")
    if tool == "place_item" and (entry.get("input") or {}).get("target", result.get("target")) == "basket":
        return result.get("item")
    return None


def unpacked_units(items, log):
    """{item: qty} ordered but not in the basket, from the order items and its step log.
    Extra units (more than ordered) do not count against other items."""
    ordered = {}
    for entry in items:
        ordered[entry["item"]] = ordered.get(entry["item"], 0) + entry["qty"]
    packed = {}
    for entry in log or []:
        item = basket_item(entry)
        if item:
            packed[item] = packed.get(item, 0) + 1
    return {item: qty - min(packed.get(item, 0), qty) for item, qty in ordered.items()
            if qty - min(packed.get(item, 0), qty) > 0}


class OrderTracker:
    def __init__(self, items, inventory):
        self.inventory = inventory
        self.ordered = {}
        for entry in items:
            self.ordered[entry["item"]] = self.ordered.get(entry["item"], 0) + entry["qty"]
        self.packed = {item: 0 for item in self.ordered}
        self.extra = {}
        self.not_picked = {}         # item -> units given up after the pick retries
        self.not_picked_reason = {}  # item -> the robot's last message
        self.holding = None
        self.needs_operator = None   # why a person must check the arm (the gripper holds something)
        self.station = None  # unknown until the robot tells us

    # ---------- Questions ----------

    def shelf_of(self, item):
        return self.inventory.get(item, {}).get("shelf")

    def missing(self):
        """[{"item", "qty"}] still to pack (units given up as not picked are not missing any more)."""
        return [
            {"item": item, "qty": qty - self.packed[item] - self.not_picked.get(item, 0)}
            for item, qty in self.ordered.items()
            if self.packed[item] + self.not_picked.get(item, 0) < qty
        ]

    def given_up(self):
        """[{"item", "qty", "reason"}] reported as not picked."""
        return [{"item": item, "qty": qty, "reason": self.not_picked_reason.get(item, "")}
                for item, qty in self.not_picked.items() if qty > 0]

    def extras(self):
        """[{"item", "qty"}] in the basket that the customer did not order."""
        return [{"item": item, "qty": qty} for item, qty in self.extra.items() if qty > 0]

    def is_complete(self):
        """Done = every item packed exactly as ordered, nothing extra, nothing given up, robot holds nothing."""
        return not self.missing() and not self.extras() and not self.given_up() and self.holding is None

    def packed_count(self):
        return sum(self.packed.values())

    # ---------- Guards (called BEFORE a robot command) ----------

    def check_pick(self, item):
        """Return None if the pick is allowed, else a 'REFUSED: ...' message for Nemotron."""
        if self.holding:
            return (f"REFUSED: the robot reports it is holding {self.holding}. "
                    f"Do not pick: the operator must check the arm.")
        ordered = self.ordered.get(item, 0)
        if ordered == 0:
            return f"REFUSED: {item} is not in this order. Do not pick it."
        if self.packed[item] >= ordered:
            return (f"REFUSED: {item} is already complete ({self.packed[item]}/{ordered} packed). "
                    f"Do not pick more {item}.")
        if self.not_picked.get(item):
            return (f"REFUSED: {item} could not be picked and is reported as not picked. "
                    f"Do not pick it again; continue with the other items.")
        shelf = self.shelf_of(item)
        if self.station is not None and shelf and self.station != shelf:
            return (f"REFUSED: the robot is at {self.station}, but {item} is at {shelf}. "
                    f"Call navigate_to {shelf} first.")
        return None

    # ---------- Updates (ONLY from real robot results) ----------

    def sync_from_status(self, status):
        """Take holding and station from the robot's /status answer."""
        if not isinstance(status, dict) or "holding" not in status:
            return
        self.holding = status.get("holding")
        if status.get("station"):
            self.station = status["station"]

    def record(self, tool, args, result):
        """Update after a robot command. Returns 'packed', 'extra' or None (for a unit in the basket)."""
        if tool == "navigate_to" and not result.get("success") and not result.get("refused"):
            self.station = None   # the robot may have moved: unknown until it arrives somewhere
            return None
        if tool == "pick_item" and result.get("not_picked"):
            item = result.get("item") or args.get("item")
            if item in self.ordered:
                self.not_picked[item] = self.ordered[item] - self.packed[item]
                self.not_picked_reason[item] = result.get("message", "")
            if result.get("needs_operator"):
                self.holding = item
                self.needs_operator = result.get("message") or f"the gripper still holds {item}"
            return None
        if not result.get("success"):
            return None
        if tool == "navigate_to":
            self.station = args.get("station")
            return None
        entry = {"tool": tool, "input": args, "result": result}
        item = basket_item(entry)
        if item:
            if self.packed.get(item, 0) < self.ordered.get(item, 0):
                self.packed[item] += 1
                return "packed"
            self.extra[item] = self.extra.get(item, 0) + 1
            return "extra"
        if tool == "pick_item":
            # The robot says "picked" but not "in the basket": it still holds it (old contract)
            self.holding = result.get("item") or args.get("item")
        return None

    # ---------- Progress line for Nemotron ----------

    def progress_line(self):
        parts = [f"{item} {self.packed[item]}/{qty} packed" for item, qty in self.ordered.items()]
        text = "Progress: " + ", ".join(parts) + "."
        if self.given_up():
            text += " NOT PICKED (reported, do not pick again): " + ", ".join(
                f"{g['qty']} {g['item']}" for g in self.given_up()) + "."
        if self.holding:
            text += f" Robot reports it HOLDS {self.holding}."
        text += f" Robot at: {self.station or 'unknown'}."
        if self.extras():
            text += " EXTRA in basket (not ordered): " + ", ".join(f"{e['qty']} {e['item']}" for e in self.extras()) + "."
        if not self.missing() and not self.holding:
            text += (" All items are packed." if not self.given_up() else
                     " Nothing left to pick (the rest is reported as not picked).")
            text += " Next: navigate_to customer, then home, then say DONE."
        return text

