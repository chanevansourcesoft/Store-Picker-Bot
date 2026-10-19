"""
Order tracker: the CODE keeps the truth about an order, not the model.

Per item it knows: ordered qty, packed qty (in the basket), and extra qty
(put in the basket but not ordered). It also knows what the robot holds now
and where the robot is.

It changes ONLY from real robot results (record() and sync_from_status()).
The tools ask it before a robot command (check_pick, check_place). If a command
would be a mistake, it is refused and the robot never gets it.

Example:
    tracker = OrderTracker([{"item": "soup_can", "qty": 2}], inventory)
    tracker.check_pick("soup_can")   # None = allowed, or "REFUSED: ..." text
    tracker.progress_line()          # "Progress: soup_can 0/2 packed. Holding: nothing. ..."
"""

import re


class OrderTracker:
    def __init__(self, items, inventory):
        self.inventory = inventory
        self.ordered = {}
        for entry in items:
            self.ordered[entry["item"]] = self.ordered.get(entry["item"], 0) + entry["qty"]
        self.packed = {item: 0 for item in self.ordered}
        self.extra = {}
        self.holding = None
        self.station = None  # unknown until the robot tells us

    # ---------- Questions ----------

    def shelf_of(self, item):
        return self.inventory.get(item, {}).get("shelf")

    def missing(self):
        """[{"item", "qty"}] still to pack."""
        return [
            {"item": item, "qty": qty - self.packed[item]}
            for item, qty in self.ordered.items()
            if self.packed[item] < qty
        ]

    def extras(self):
        """[{"item", "qty"}] in the basket that the customer did not order."""
        return [{"item": item, "qty": qty} for item, qty in self.extra.items() if qty > 0]

    def is_complete(self):
        """Done = every item packed exactly as ordered, nothing extra, robot holds nothing."""
        return not self.missing() and not self.extras() and self.holding is None

    def packed_count(self):
        return sum(self.packed.values())

    # ---------- Guards (called BEFORE a robot command) ----------

    def check_pick(self, item):
        """Return None if the pick is allowed, else a 'REFUSED: ...' message for Nemotron."""
        if self.holding:
            return (f"REFUSED: the robot is already holding {self.holding}. "
                    f"Call scan_item, then place_item first.")
        ordered = self.ordered.get(item, 0)
        if ordered == 0:
            return f"REFUSED: {item} is not in this order. Do not pick it."
        if self.packed[item] >= ordered:
            return (f"REFUSED: {item} is already complete ({self.packed[item]}/{ordered} packed). "
                    f"Do not pick more {item}.")
        return None

    def check_place(self, target):
        """Return None if the place is allowed, else a 'REFUSED: ...' message for Nemotron."""
        item = self.holding
        if not item:
            return None  # the robot itself will answer "not holding anything"
        if target == "basket":
            ordered = self.ordered.get(item, 0)
            if self.packed.get(item, 0) >= ordered:
                return (f"REFUSED: {item} is already complete ({self.packed.get(item, 0)}/{ordered} packed). "
                        f"Do not put it in the basket. Return it with place_item('return') at {self.shelf_of(item)}.")
        if target == "return":
            shelf = self.shelf_of(item)
            if self.station != shelf:
                return (f"REFUSED: {item} belongs on {shelf}, but the robot is at {self.station}. "
                        f"Call navigate_to('{shelf}') first, then place_item('return').")
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
        """Update after a robot command. Returns 'packed', 'extra' or None (for place in basket)."""
        if not result.get("success"):
            return None
        if tool == "navigate_to":
            self.station = args.get("station")
        elif tool == "pick_item":
            self.holding = args.get("item")
        elif tool == "place_item":
            item = _item_from_message(result.get("message", "")) or self.holding
            self.holding = None
            if args.get("target") == "basket" and item:
                if self.packed.get(item, 0) < self.ordered.get(item, 0):
                    self.packed[item] += 1
                    return "packed"
                self.extra[item] = self.extra.get(item, 0) + 1
                return "extra"
        return None

    # ---------- Progress line for Nemotron ----------

    def progress_line(self):
        parts = [f"{item} {self.packed[item]}/{qty} packed" for item, qty in self.ordered.items()]
        text = "Progress: " + ", ".join(parts) + "."
        text += f" Holding: {self.holding or 'nothing'}. Robot at: {self.station or 'unknown'}."
        if self.extras():
            text += " EXTRA in basket (not ordered): " + ", ".join(f"{e['qty']} {e['item']}" for e in self.extras()) + "."
        if not self.missing() and not self.holding:
            text += " All items are packed. Next: navigate_to customer, then home, then say DONE."
        return text


def _item_from_message(message):
    """'placed soup_can in basket' / 'returned soup_can to shelf' -> 'soup_can'."""
    match = re.search(r"(?:placed|returned) (\S+) (?:in|to)", message)
    return match.group(1) if match else None
