"""
lerobot-train for GR00T, started by train_groot.sh and resume_groot.sh instead of lerobot-train itself.

Two differences:

1. GR00T's training-time image augmentation (a random crop per sample, the same for both cameras)
stays on when the run starts from a saved model, that is with FROM_MODEL or on a resume. LeRobot
(0.6.1 and 0.6.2) loads the saved processors of such a model in evaluation mode, so without this
those runs would train with a fixed centre crop, unlike a new run. A new run from NVIDIA's base
model is not changed. The saved model is not changed either: when a model is loaded to run it
(inference), its processors are set to evaluation mode again.

2. The weights being trained are kept in fp32 (the frozen backbone stays in bf16, so it fits in
memory). With bf16 weights, an optimizer step of about the learning rate rounds away to nothing on
any weight above about 0.03: comparing two checkpoints 20,000 steps apart, 2.6% of the weights
between 0.03 and 0.1 had changed, and 0.1% of the larger ones. Computing stays in bf16 (autocast).

With SPLIT_BACKBONE=1 and two visible GPUs, the frozen backbone runs on the second GPU, the rest on the
first, so the first has room to train the diffusion model. This is not LeRobot's two-GPU training
(a full copy of the model on each): one process, one model, in two parts.
"""

import os

import torch
from accelerate import Accelerator

import lerobot.policies.groot.groot_n1_7 as groot_n1_7
import lerobot.policies.groot.modeling_groot as groot_modeling
import lerobot.policies.groot.processor_groot as groot_processor
from lerobot.scripts import lerobot_train

_set_mode = groot_processor._set_groot_preprocessor_training


def _training_mode(preprocessor, *, training):
    # lerobot-train builds the processors only to train with them.
    _set_mode(preprocessor, training=True)


groot_processor._set_groot_preprocessor_training = _training_mode

_create_model = groot_modeling.GrootPolicy._create_groot_model


def _create_model_fp32_trained(self):
    model = _create_model(self)
    for parameter in model.parameters():
        if parameter.requires_grad and parameter.is_floating_point():
            parameter.data = parameter.data.to(torch.float32)
    return model


groot_modeling.GrootPolicy._create_groot_model = _create_model_fp32_trained


def _forward_split(self, inputs):
    # prepare_input sends everything to the first weights' GPU, which is the backbone's.
    backbone_inputs, action_inputs = self.prepare_input(inputs)
    backbone_outputs = self.backbone(backbone_inputs)
    device = next(self.action_head.parameters()).device
    return self.action_head(backbone_outputs.to(device), action_inputs.to(device))


_prepare = Accelerator.prepare


def _prepare_split(self, *args, **kwargs):
    prepared = _prepare(self, *args, **kwargs)
    for item in prepared if isinstance(prepared, tuple) else (prepared,):
        if isinstance(item, groot_modeling.GrootPolicy):
            item._groot_model.backbone.to("cuda:1")
            torch.cuda.empty_cache()
    return prepared


class _AdamWOneByOne(torch.optim.AdamW):
    # By default Adam works on all weights at once, with a temporary copy of its stats (6.5 GB here).
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **{**kwargs, "foreach": False})


if os.environ.get("SPLIT_BACKBONE") == "1":
    groot_n1_7.GR00TN17.forward = _forward_split
    Accelerator.prepare = _prepare_split
    torch.optim.AdamW = _AdamWOneByOne

if __name__ == "__main__":
    lerobot_train.main()
