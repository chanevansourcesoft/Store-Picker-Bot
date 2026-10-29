# Store Picker Bot app: record and review without a terminal

**Store Picker Bot** is a desktop app for recording demonstrations on the SO-100 arms. One person
can use it from start to end: choose what to record, check the arms and cameras, record round by
round with a live camera view, review every episode, then send the dataset to the training PC and
train GR00T there. It uses `record.py` behind the scenes,
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
   ~/Documents/Store-Picker-Bot/policy/studio/install_desktop_icon.sh   # change the path to where you cloned the repo
   ```
5. Optional: take a photo of the arm in its rest pose and save it as `policy/studio/web/rest_pose.jpg`.
   The Record page then shows it.

6. For the **Train** step: connect this PC to the training PC once (see "Training PC" below).

Settings (default rounds and episodes, the long-episode warning, minimum free disk space, the training
PC) are in `policy/studio/studio_config.json`.

## For the operator

Open **Store Picker Bot** from the desktop or the applications menu. The bar at the top shows the
steps; a step unlocks when the one before it is done.

1. **Campaign:** a campaign is one dataset with its recording plan. Every time the app opens, pick
   the campaign and press **Open campaign** (the one used last is already selected), or create a new
   one: a name, the products, how many rounds (base positions) and how many episodes of each
   product per round. **Delete campaign** moves the selected campaign's dataset to the Trash of the
   computer; restore it from the Trash if that was a mistake.
   - **Change rounds** (under the selected campaign) sets how many rounds it has, up to 15. Episodes per
     product per round cannot change. Rounds that already have episodes stay, with their base positions;
     new rounds get base positions not used yet. Not possible while a round is running.
2. **Check:** every line must be green; a red line says what to do.
   - Press **Take test pictures**: each camera must show its own view, sharp.
   - Press **Start test** and move every joint of the leader arm a little, including the gripper,
     until all six are green. The follower arm must say it answers.

   Then press **Continue to recording**.
3. **Record:** the right side says where to put the base (a round) and which product to pick.
   Below the product is what to change between episodes (position, angle, neighbours): see
   "Change the product setup between episodes" in [DATA_COLLECTION.md](DATA_COLLECTION.md).
   - Put the base there, using the floor marks, and the leader arm in its rest pose. Press
     **Start round**. The follower arm moves to the leader arm's pose when the round starts.
   - For each episode: press **Space** (Start) just before you move, pick the product shown, put
     it in the basket, bring the arm back to its rest pose, then press **N** (Save). Press **X**
     (Cancel) to throw a try away. Wait for **Ready** before the next one.
   - The live view shows both cameras; a red frame means it is recording.
   - When the round is complete, put the follower arm in its rest pose and press
     **The arm is at rest: finish the round**. Its motors switch off then.
   - **Pause round** stops early (the episode in progress is dropped, saved ones are kept). The button
     then says **Resume round**: press it later, even another day, and it continues where it stopped
     (same base position, the episodes still missing).
4. **Review:** watch every episode of the round (both cameras side by side) and decide:
   - **Approve** (`A`) if it is a clean demonstration;
   - **Flag as bad** (`F`) and pick the reason if not. A flagged episode is recorded again in the
     next round, with the base still at the same place.

   Yellow warnings point at likely problems (for example "does not end in the usual rest pose").
   When every episode has a decision, press **Next round**.
5. Repeat **Record → Review** until the plan is complete. At the end, if some episodes were flagged,
   press **Remove flagged episodes** on the Review page. They are deleted for good (no backup is kept),
   and the dataset is renumbered.

6. **Train:** opens once the campaign has episodes.
   - **Send dataset** copies the dataset to the training PC. Every episode must be reviewed and the
     flagged ones removed first. The app sends over 4 connections at once, which is about 4 times faster
     when the network limits each connection (500 MB took about 2 minutes instead of 8); it shows the
     progress. Sending again later only copies what changed, and removes files the dataset no longer has.
   - Choose the number of **training steps** (default 50,000) and press **Start training**. The page
     shows how long it takes and how much disk space it needs at most. It refuses to start when the
     dataset there is not the same as here, a training is already running there, the GPU is in use,
     or the disk is too full.
   - **Training runs** lists every run on the training PC: its step, loss, time left, checkpoints and
     size. Click a run to see its loss curve. **Stop training** stops it; its checkpoints stay.
     A stopped run shows when it was stopped and from where (this app, or a terminal on which computer).
   - **Resume from step …** appears for a stopped or failed run: it continues the run from its newest
     complete checkpoint, with the same steps, settings and learning-rate schedule (the steps after
     that checkpoint are trained again). It needs the same checks as Start training.
   - **Copy to this PC** copies the model of the chosen checkpoint (about 9 GB) into
     `policy/inference/models/<run>_<step>/`, to run it on the arm ([INFERENCE.md](INFERENCE.md), from step 3:
     `--model <run>_<step>`). It uses 8 connections at once (about 15–20 minutes) and checks that the copy is
     exact. Recording can go on meanwhile. A stopped or failed copy can be started again.

   The training runs on the training PC: it goes on when the app or this computer is closed. Open the
   Train step again at any time to follow it.

7. **Run:** runs a trained model on the arm (open at any time, whatever the campaign). Copy a model to this PC
   first (Train step, **Copy to this PC**).
   - Choose the **model**, the **product**, and the settings: **speed** (1 = as trained; 0.8 plays the
     movements slower), **smoothing** (0.3), **blend steps** (8), **commands per target** (3), **acceleration
     limit** (0 = off), **wait for the model to act** (0 = off), **end after the drop** (0.5 s) and the **time limit** per
     episode. They are
     explained in [INFERENCE.md](INFERENCE.md). Tick **Dry run** the first time with a new model: the model
     runs, the arm does not move.
   - **Load the model** (about a minute). The live view shows both cameras.
   - Put the arm in its rest pose and the product on the shelf, then **Start** (`Space`). **Stop** (`Space`)
     stops the arm where it is; **Back to start** (`R`) moves it slowly back to where the episode started.
     After the drop in the basket the episode ends by itself and the arm goes back to rest.
   - Between episodes, change the product or the settings and press **Use these for the next episode**:
     the model stays loaded. **Last episode** shows how long it took and how smooth it was.
   - When the settings work well, press **Save as default**: they are filled in next time, and `run.sh` and the
     policy worker (the robot's picks) use them too ([INFERENCE.md](INFERENCE.md#options)).
   - **Quit** moves the arm back to where the episode started, then switches its motors off.
   While a model runs, recording is not possible (the arm is in use).

You can stop at any time and continue another day: everything is saved in the dataset folder.

## Training PC (admin, once)

The Train step works over SSH from this PC to the training PC.

1. On the training PC: the project is cloned and set up for training ([TRAINING.md](TRAINING.md), step 1),
   and `sudo apt install openssh-server` if SSH is not installed.
2. On this PC, allow it to log in there without a password (it asks for the training PC's password
   once, and asks to trust the computer: answer `yes`):
   ```bash
   ssh-copy-id <user>@<address of the training PC>
   ssh <user>@<address of the training PC> echo ok     # must print ok without asking anything
   ```
   On the training PC, `whoami` shows the user and `hostname -I` the address.
3. In `policy/studio/studio_config.json`, block `"training"`:

   | Setting | Meaning |
   |---|---|
   | `host` | `<user>@<address>` of the training PC |
   | `repo` | where the project is on the training PC (full path) |
   | `conda_env` | conda env used for training there (empty: `policy/.venv`, see TRAINING.md) |
   | `gpu`, `batch_size`, `memory_mode` | training settings, as for `train_groot.sh` ([TRAINING.md](TRAINING.md), step 4) |
   | `default_steps` | steps shown by default |
   | `save_freq` | a checkpoint every this many steps |
   | `keep_last` | the newest checkpoints kept complete (model and resume state) |
   | `keep_model_every` | older checkpoints at a multiple of this many steps keep only their model; the others are deleted (0: delete all older ones) |
   | `sec_per_step` | used for the time estimate until the training PC has a run to measure |
   | `send_streams` | connections used at once to send a dataset (default 4) |
   | `fetch_streams` | connections used at once to copy a model back (default 8) |

4. Keep both PCs on the same version of the project (`git pull` on both): the Train step uses scripts
   in `policy/training/` on the training PC, and says so if they are missing.

The app never deletes training runs on the training PC. Delete old runs there by hand when the disk
gets full (`policy/training/outputs/<run>/`).

## Where things are

| What | Where |
|---|---|
| Datasets | `policy/data_collection/dataset/<name>_<DDMMYYYY_HHMMSS>/` |
| Plan, and per episode its product, round, review decision and flag reason | `meta/plan.json`, `meta/episode_log.json` in the dataset |
| Last campaign | `~/.config/store_picker_studio/state.json` |
| App log (for the admin) | `~/.cache/store_picker_studio.log` |
| Training runs | on the training PC: `policy/training/outputs/<run>/`, log in `policy/training/logs/<run>.log` |

The app runs only on this computer (`127.0.0.1:8780`). It stops by itself a few minutes after its
window is closed, but never while a round runs or a dataset is being sent.

## Good to know

- The app and a terminal recording cannot run at the same time: only one program can open the
  arms. The Check step says so if a terminal recording or teleoperation is still running.
- Review decisions are stored in the episode log: `approved`, and `bad` (the same mark as key B in
  the terminal) with `bad_reason`. Episodes recorded in the terminal show up in the review too.
- Moving the dataset to the training machine: [DATA_COLLECTION.md](DATA_COLLECTION.md), step 8.
