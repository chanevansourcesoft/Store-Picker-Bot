# Inference

Running a trained GR00T model on the real arm. Guide: [INFERENCE.md](../docs/INFERENCE.md).

| File / folder | What it is |
|---|---|
| `run_policy.py` | Runs the model on the follower arm with both cameras, controlled with keys (Space, R, Q). The same on every machine |
| `run_policy_studio.py` | The same, for the Store Picker Bot app's Run step: buttons instead of keys (started by the app) |
| `run_settings.py` | Limits of the run settings, and the saved defaults (`run_settings.json`, from the app's **Save as default**) |
| `pick_worker.py` | The policy worker for the robot server: keeps the model loaded, runs one pick per request (local web port 8790) |
| `pick_client.py` | What the robot server calls: `run_pick`, `stop_arm`, `arm_ready` (no LeRobot needed) |
| `pc/run.sh`, `pc/pick_worker.sh` | Start them on a PC, without Docker (for testing) |
| `jetson/run.sh`, `jetson/pick_worker.sh` | Start them on the Jetson, in the policy container |
| `models/` | Trained models copied from the training PC, one folder each (not stored in Git) |
