# Requests to the policy team

Things the robot server would like changed in `policy/`. We do not edit `policy/` ourselves.

## 1. A reason code in the pick result

`pick_client.run_pick()` returns `(ok, message)` only. To know whether to retry, stop the
order or call a person, `arm.classify()` reads the start of the message text
(`no grasp seen`, `time limit`, `stopped`, `unknown item`, `busy`, and `still holds something`
anywhere in it). If the wording in `pick_worker.py` changes, the robot server misreads the
result (an unknown message counts as "not ready": no retry, no arm motion).

Request: add a fixed field to the `/pick` answer, for example
`"reason": "placed" | "no_grasp" | "time_limit" | "holding" | "stopped" | "unknown_item" | "busy" | "not_ready" | "policy_error"`,
and a way to get it from `pick_client` (for example `pick(item) -> dict` next to `run_pick`).
Until then, `robot_server/test_jetson_pick.py` (`test_classify_matches_the_policy_worker`) pins
today's messages.

## 2. Name the shelf level "level" in instructions.json (optional)

In `instructions.json`, `"shelf"` is a shelf level on the rack (`upper_shelf`, `lower_shelf`),
while everywhere else in the repo a shelf is a Nav2 station (`shelf_A`). The robot server no
longer compares the two, but the name is easy to misread. If you rename it to `"level"`, tell us:
`instructions.py` and `store_agent/inventory.py` (`check_catalog`) then read `"level"`.
