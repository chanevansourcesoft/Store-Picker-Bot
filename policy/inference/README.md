# Inference

Running a trained GR00T model on the real arm. Guide: [INFERENCE.md](../docs/INFERENCE.md).

| File / folder | What it is |
|---|---|
| `run_policy.py` | Runs the model on the follower arm with both cameras, controlled with keys (Space, R, Q). The same on every machine |
| `run_policy_studio.py` | The same, for the Store Picker Bot app's Run step: buttons instead of keys (started by the app) |
| `pc/run.sh` | Starts it on a PC, without Docker (for testing) |
| `jetson/run.sh` | Starts it on the Jetson, in the policy container |
| `models/` | Trained models copied from the training PC, one folder each (not stored in Git) |
