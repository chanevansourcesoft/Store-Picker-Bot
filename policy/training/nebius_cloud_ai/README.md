# Training on Nebius AI Cloud

Plan for moving GR00T training from the local PC to Nebius AI Cloud. Nothing here is set up or tested yet.

## Why

The local PC has 32 GB per GPU, which is not enough for NVIDIA's full GR00T recipe
(fp32 weights, diffusion model trained: about 40 GB or more per GPU). A Nebius GPU VM with
80 GB or more per GPU (for example H100 or H200) can run `MEMORY_MODE=full` with larger batches.

## Plan

The training scripts in `../local/` work on any Linux machine with NVIDIA GPUs, so the cloud run reuses them:

1. Create a GPU VM on Nebius AI Cloud with an Ubuntu image that includes NVIDIA drivers, and enough disk
   for the datasets and checkpoints (about 11 GB per checkpoint).
2. On the VM, clone the repo and run `policy/training/local/setup.sh`, as on the local PC
   ([TRAINING_LOCAL.md](../../docs/TRAINING_LOCAL.md), step 1).
3. Get the dataset: download it from the Hugging Face Hub, or copy it with `rsync` into
   `policy/data_collection/dataset/` ([DATA_COLLECTION.md](../../docs/DATA_COLLECTION.md), step 8).
4. Train with the full recipe, for example on a VM with 8 GPUs:
   ```bash
   cd policy/training/local
   MEMORY_MODE=full NUM_GPUS=8 BATCH_SIZE=16 ./train_groot.sh <dataset folder>
   ```
5. Copy the checkpoint to keep (`checkpoints/<step>/pretrained_model/`) back with `rsync`, or upload it
   to the Hugging Face Hub, then delete the VM so it stops costing money.

## To decide

- Which GPU type and how many (cost against training time).
- Whether to move datasets and checkpoints through the Hugging Face Hub or Nebius Object Storage.
- Whether this folder needs its own scripts (for example VM setup) or the local ones are enough.
