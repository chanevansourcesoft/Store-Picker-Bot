# Store Picker Bot app: record and review without a terminal

**Store Picker Bot** is a desktop app for recording demonstrations on the SO-100 arms. One person
can use it from start to end: choose what to record, check the arms and cameras, record round by
round with a live camera view, and review every episode. It uses `record.py` behind the scenes,
so the dataset, its plan and its episode log are exactly the same as when you record in a terminal
([DATA_COLLECTION.md](DATA_COLLECTION.md)). Both ways can be used on the same dataset.

The app runs on a PC without Docker (not in the Jetson container).

## For the admin (once per computer)

1. Set up the PC as in "Getting started without Docker" in [README.md](../README.md), with the conda
   environment `lerobot_env` (or `policy/.venv`).
2. Do the one-time steps of [DATA_COLLECTION.md](DATA_COLLECTION.md), step 2: the four devices in the
   repo root `.env`, `camera_settings.json`, the floor marks. Calibrate both arms
   ([ARM_SETUP.md](ARM_SETUP.md)). Operators never do these.
3. Give your user access to the arm ports once, so it works after every reboot:
   ```bash
   sudo usermod -aG dialout $USER     # then log out and back in
   ```
4. Add the app to the applications menu and the desktop:
   ```bash
   cd path/to/Store-Picker-Bot/policy/studio
   ./install_desktop_icon.sh
   ```
5. Optional: take a photo of the arm in its rest pose and save it as `policy/studio/web/rest_pose.jpg`.
   The Record page then shows it.

Settings (default rounds and episodes, the long-episode warning, minimum free disk space) are in
`policy/studio/studio_config.json`.

## For the operator

Open **Store Picker Bot** from the desktop or the applications menu. The bar at the top shows the
steps; a step unlocks when the one before it is done.

1. **Campaign:** a campaign is one dataset with its recording plan. Every time the app opens, pick
   the campaign and press **Open campaign** (the one used last is already selected), or create a new
   one: a name, the products, how many rounds (base positions) and how many episodes of each
   product per round.
2. **Check:** every line must be green; a red line says what to do.
   - Press **Take test pictures**: each camera must show its own view, sharp.
   - Press **Start test** and move every joint of the leader arm a little, including the gripper,
     until all six are green. The follower arm must say it answers.

   Then press **Continue to recording**.
3. **Record:** the right side says where to put the base (a round) and which product to pick.
   - Put the base there, using the floor marks, and the leader arm in its rest pose. Press
     **Start round**. The follower arm moves to the leader arm's pose when the round starts.
   - For each episode: press **Space** (Start) just before you move, pick the product shown, put
     it in the basket, bring the arm back to its rest pose, then press **N** (Save). Press **X**
     (Cancel) to throw a try away. Wait for **Ready** before the next one.
   - The live view shows both cameras; a red frame means it is recording.
   - When the round is complete, put the follower arm in its rest pose and press
     **The arm is at rest: finish the round**. Its motors switch off then.
   - **Pause round** stops early (the episode in progress is dropped, saved ones are kept). Start the
     round again later, even another day: it continues where it stopped.
4. **Review:** watch every episode of the round (both cameras side by side) and decide:
   - **Approve** (`A`) if it is a clean demonstration;
   - **Flag as bad** (`F`) and pick the reason if not. A flagged episode is recorded again in the
     next round, with the base still at the same place.

   Yellow warnings point at likely problems (for example "does not end in the usual rest pose").
   When every episode has a decision, press **Next round**.
5. Repeat **Record → Review** until the plan is complete. At the end, if some episodes were flagged,
   press **Remove flagged episodes** on the Review page. The dataset is then renumbered, and the old
   copy is kept next to it as `<dataset>_old` (the admin deletes it after checking).

You can stop at any time and continue another day: everything is saved in the dataset folder.

## Where things are

| What | Where |
|---|---|
| Datasets | `policy/data_collection/dataset/<name>_<DDMMYYYY_HHMMSS>/` |
| Plan, and per episode its product, round, review decision and flag reason | `meta/plan.json`, `meta/episode_log.json` in the dataset |
| Last campaign | `~/.config/store_picker_studio/state.json` |
| App log (for the admin) | `~/.cache/store_picker_studio.log` |

The app runs only on this computer (`127.0.0.1:8780`). It stops by itself a few minutes after its
window is closed, but never while a round runs.

## Good to know

- The app and a terminal recording cannot run at the same time: only one program can open the
  arms. The Check step says so if a terminal recording or teleoperation is still running.
- Review decisions are stored in the episode log: `approved`, and `bad` (the same mark as key B in
  the terminal) with `bad_reason`. Episodes recorded in the terminal show up in the review too.
- Moving the dataset to the training machine: [DATA_COLLECTION.md](DATA_COLLECTION.md), step 8.
