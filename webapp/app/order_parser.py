"""
Step 2.2: Turn a customer order (normal language) into a clean item list.

Example:
    "2 soup cans and one chips"  ->  {"items": [{"item": "soup_can", "qty": 2},
                                                {"item": "chips_pack", "qty": 1}],
                                      "unknown": []}

Run a quick test:
    python -m app.order_parser "2 soup cans and one chips"
"""

import json
import re
import sys

from app.config import MODEL_FAST, get_client, has_llm, load_inventory

SYSTEM_PROMPT = """You read grocery orders for a small store.
Products we sell (use these exact names): {products}

Turn the customer's order into JSON with this exact shape:
{{"items": [{{"item": "<product name>", "qty": <number>}}], "unknown": ["<things we do not sell>"]}}

Rules:
- Only use product names from the list above.
- Match loose words to the right product (for example "soup" -> "soup_can", "chips" -> "chips_pack").
- If the customer asks for something we do not sell, put their words in "unknown".
- If no number is given, qty is 1.
- Reply with JSON only. No other text."""


def extract_json(text):
    """Find the JSON object in the model reply (the model may add extra text)."""
    # Remove any <think>...</think> reasoning block
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in model reply: {text!r}")
    return json.loads(match.group(0))


def clean_order(data, inventory):
    """Keep only known products with a quantity of 1 or more. Merge duplicates."""
    items = {}
    unknown = list(data.get("unknown", []))

    for entry in data.get("items", []):
        name = str(entry.get("item", "")).strip()
        try:
            qty = int(entry.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1

        if name not in inventory:
            unknown.append(name)
        elif qty > 0:
            items[name] = items.get(name, 0) + qty

    return {
        "items": [{"item": k, "qty": v} for k, v in items.items()],
        "unknown": unknown,
    }


NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "couple": 2, "pair": 2}
FILLER_WORDS = {"please", "i", "want", "need", "would", "like", "some", "get", "me", "give", "of",
                "the", "and", "also", "plus", "with", "can", "could", "you", "x", "pcs", "pieces"}


def parse_order_offline(order_text, inventory):
    """Keyword parser for when Nemotron is not available (no API key).

    "2 soups, a juice and pizza" -> soup_can x2, juice_box x1, unknown ["pizza"].
    A product matches on the first word of its name ("soup" for soup_can), plural or not.
    """
    keywords = {name.split("_")[0]: name for name in inventory}
    data = {"items": [], "unknown": []}
    for chunk in re.split(r",|;|\band\b|\bplus\b|&|\n", order_text.lower()):
        words = re.findall(r"[a-z]+|\d+", chunk)
        qty, item, rest = 1, None, []
        for word in words:
            if word.isdigit():
                qty = int(word)
            elif word in NUMBER_WORDS:
                qty = NUMBER_WORDS[word]
            elif item is None and (word in keywords or word.rstrip("s") in keywords
                                   or word.removesuffix("es") in keywords):
                item = keywords.get(word) or keywords.get(word.rstrip("s")) or keywords.get(word.removesuffix("es"))
            elif word not in FILLER_WORDS:
                rest.append(word)
        if item:
            data["items"].append({"item": item, "qty": qty})
        elif rest:
            data["unknown"].append(" ".join(rest))
    return clean_order(data, inventory)


def parse_order(order_text):
    inventory = load_inventory()
    if not has_llm():
        return parse_order_offline(order_text, inventory)
    client = get_client()

    response = client.chat.completions.create(
        model=MODEL_FAST,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(products=", ".join(inventory))},
            {"role": "user", "content": order_text},
        ],
        temperature=0,
        max_tokens=2000,
    )
    reply = response.choices[0].message.content or ""
    return clean_order(extract_json(reply), inventory)


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or "2 soup cans and one chips please"
    print("Order :", text)
    print("Result:", json.dumps(parse_order(text), indent=2))