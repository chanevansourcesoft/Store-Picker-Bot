"""
Products and stock.

catalog.json (in Git) says what the store sells: name, shelf, bin, price, weight, aliases.
Its item ids and shelves must match policy/instructions.json (the GR00T pick instructions).

The live stock is runtime state, so it is NOT in Git: it lives in STOCK_FILE
(default <repo>/data/stock.json). A product missing there starts at its
initial_stock from the catalog. Both apps may share the file, so every change
holds a file lock.

    load_catalog()      -> {item: {...}}               what we sell (no stock)
    load_inventory()    -> {item: {..., "stock": n}}   catalog + live stock
    get_stock()         -> {item: n}
    take_stock(item, n) -> True, or False if not enough
    add_stock(item, n)  -> True, or False if unknown item
    reserve(items)      -> (ok, problems): take a whole order's stock at once, or nothing
    release(counts)     -> give back {item: n} (units an order did not pack)
    check_catalog()     -> problems compared with policy/instructions.json ([] = OK)
"""

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from store_agent.config import get_setting

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CATALOG_FILE = HERE / "catalog.json"
POLICY_INSTRUCTIONS_FILE = Path(get_setting("POLICY_INSTRUCTIONS_FILE",
                                            str(REPO_ROOT / "policy" / "instructions.json")))
STOCK_FILE = Path(get_setting("STOCK_FILE", str(REPO_ROOT / "data" / "stock.json")))

_lock = threading.Lock()


def load_catalog():
    return json.loads(CATALOG_FILE.read_text())["products"]


def product_name(item):
    """Display name from the catalog ("hide_and_seek" -> "Hide & Seek")."""
    info = load_catalog().get(item)
    return info["name"] if info else str(item or "?").replace("_", " ").title()


def default_inventory():
    """Catalog with every product at its initial stock (fresh store, used by tests)."""
    return {item: dict(info, stock=info.get("initial_stock", 0)) for item, info in load_catalog().items()}


DEFAULT_INVENTORY = default_inventory()


# ---------- live stock ----------

def _read(path, catalog):
    try:
        saved = json.loads(path.read_text())
    except FileNotFoundError:
        saved = {}
    except json.JSONDecodeError:
        print(f"[Stock] {path} is not valid JSON: starting from the catalog's initial stock.")
        saved = {}
    return {item: int(saved.get(item, info.get("initial_stock", 0))) for item, info in catalog.items()}


def _write(path, stock):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stock, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def _stock():
    """Read, change and save the stock under a thread lock and a file lock."""
    path = Path(STOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path.with_suffix(".lock"), "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        existed = path.exists()
        stock = _read(path, load_catalog())
        before = dict(stock)
        yield stock
        if stock != before or not existed:
            _write(path, stock)


def ensure_stock():
    """Create the stock file from the catalog if it is missing. Returns True if it did."""
    existed = Path(STOCK_FILE).exists()
    with _stock():
        pass
    return not existed


def get_stock():
    with _stock() as stock:
        return dict(stock)


def take_stock(item, qty=1):
    with _stock() as stock:
        if item not in stock or stock[item] < qty:
            return False
        stock[item] -= qty
        return True


def add_stock(item, qty=1):
    with _stock() as stock:
        if item not in stock:
            return False
        stock[item] += qty
        return True


def _counts(items):
    """[{"item", "qty"}] or {item: qty} -> {item: qty}."""
    if isinstance(items, dict):
        return {item: int(qty) for item, qty in items.items()}
    counts = {}
    for entry in items:
        counts[entry["item"]] = counts.get(entry["item"], 0) + int(entry["qty"])
    return counts


def reserve(items):
    """Take the stock for a whole order when it is accepted, all or nothing, in one lock,
    so two orders can never both get the last unit. Returns (ok, problems)."""
    need = _counts(items)
    with _stock() as stock:
        problems = []
        for item, qty in need.items():
            if item not in stock:
                problems.append(f"'{item}' is not in inventory.")
            elif stock[item] < qty:
                problems.append(f"Not enough '{item}': need {qty}, have {stock[item]}.")
        if problems:
            return False, problems
        for item, qty in need.items():
            stock[item] -= qty
        return True, []


def release(items):
    """Give back reserved units that were not packed (failed pick, stop, cancel, restart)."""
    with _stock() as stock:
        for item, qty in _counts(items).items():
            if item in stock and qty > 0:
                stock[item] += qty


def load_inventory():
    stock = get_stock()
    return {item: dict(info, stock=stock[item]) for item, info in load_catalog().items()}


# ---------- contract with the arm policy ----------

def check_catalog(catalog=None, instructions_file=None):
    """The catalog must sell exactly the items GR00T was trained on, on the same shelves."""
    catalog = catalog if catalog is not None else load_catalog()
    path = Path(instructions_file or POLICY_INSTRUCTIONS_FILE)
    try:
        policy = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return [f"cannot read {path}: {e}"]
    problems = []
    for item in sorted(set(policy) - set(catalog)):
        problems.append(f"{item} has a GR00T instruction but is not in the catalog")
    for item in sorted(set(catalog) - set(policy)):
        problems.append(f"{item} is in the catalog but has no GR00T instruction")
    for item in sorted(set(catalog) & set(policy)):
        if catalog[item].get("shelf") != policy[item].get("shelf"):
            problems.append(f"{item}: catalog shelf {catalog[item].get('shelf')} != "
                            f"policy shelf {policy[item].get('shelf')}")
    return problems
