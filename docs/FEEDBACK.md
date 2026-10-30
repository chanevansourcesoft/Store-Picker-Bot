# Feedback on Nebius and NVIDIA tools

What worked and what slowed us down while we built Store Picker Bot. Each point says what happened and what
would have helped.

## Nebius Token Factory

**What worked**

- The API is OpenAI-compatible, so the agent uses the standard `openai` client with only a different base
  URL and key. Moving between Nemotron Nano, Super and Ultra only means changing the model name.
- With all three Nemotron sizes behind one key, each job gets the model it needs: Nano reads every order
  quickly, Super does the tool calling, and Ultra runs only for reports. This kept the dashboard responsive and
  saved credits.
- Nemotron Super's tool calling held up over whole orders (drive, pick, pick, drive back) once the code sent a
  progress line after every tool call.

**What slowed us down**

- **Unclear key errors.** Two of the keys we tried were rejected with a plain `401` each time, with no reason
  given. We could not tell a wrong key format, a revoked key and a key without credits apart. An error body
  that says which one it is (and what a valid key looks like, for example its `v1.` prefix) would have saved
  us time.
- **Model names.** The model ids mix cases (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`,
  `nvidia/nemotron-3-super-120b-a12b`). A copyable list of the exact ids next to the API docs, or ids that
  ignore case, would avoid `model not found` errors.

## NVIDIA GR00T N1.7 (with LeRobot)

**What worked**

- Fine-tuning on teleoperated demonstrations from the real SO-100 arm gave a policy that does pick and place
  on our shelf, with no simulation data.
- The same model runs on the Jetson AGX Orin 64GB, so training and the robot share one model format.

**What slowed us down**

- **Image augmentation turns off on resume.** LeRobot loads a saved model's processors in evaluation mode, so
  GR00T's random image crop silently stayed off for every resumed run and every run started from a saved
  model. We only found this by reading the code, and wrote a wrapper (`policy/training/lerobot_train_groot.py`)
  to keep it on. A warning, or a flag to keep training processors, would help.
- **Memory.** Training the diffusion model with bf16 base weights does not fit one 32 GB GPU at batch 64 or 32.
  It only fits with the frozen backbone on a second GPU. A table of memory use per training mode in the GR00T
  docs would save trial runs.
- **Two GPUs can be slower than one.** With one GPU on a PCIe 4.0 x4 slot, two-GPU training ran at 44 samples
  per second against 66 on one GPU, because about 1 GB is exchanged after every step. A note in the docs about
  the slot width needed would help.

## NVIDIA Cosmos-Reason2-2B

- GR00T N1.7 uses it as its backbone, and the model is gated on Hugging Face. Each new machine (training PC,
  Jetson) needed the access terms accepted and an `HF_TOKEN` before anything ran. Saying this at the top of the
  GR00T quick start would save a failed first download.

## NVIDIA Jetson AGX Orin and ROS 2

- **Fast DDS shared memory and container users.** rosbridge started as root in its own container connected to
  ROS 2 but received no topic data, because Fast DDS shared memory needs the same user id as the other ROS
  containers (uid 1000 here). There was no error at all. A log line when shared-memory transport is refused
  would make this a quick fix.
- **Windows hosts.** On Windows, Git Bash rewrites `docker run -e VAR=/path` values into Windows paths. We
  worked around it with `MSYS_NO_PATHCONV=1` and `--env-file`. This is not an NVIDIA issue, but Jetson guides
  that show commands run from a Windows laptop could mention it.

## Summary

| Tool | Would most help |
|---|---|
| Token Factory | Error bodies that explain why a key is rejected |
| GR00T + LeRobot | Keep training augmentation on when resuming, or warn when it is off |
| GR00T docs | Memory use per training mode; the PCIe width needed for multi-GPU |
| Cosmos-Reason2-2B | Gated access mentioned up front in the GR00T quick start |
| Fast DDS on Jetson | A log line when shared memory is refused because of a user id mismatch |
