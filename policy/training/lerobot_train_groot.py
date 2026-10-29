"""
lerobot-train for GR00T, started by train_groot.sh and resume_groot.sh instead of lerobot-train itself.

The only difference: GR00T's training-time image augmentation (a random crop per sample, the same
for both cameras) stays on when the run starts from a saved model, that is with FROM_MODEL or on a
resume. LeRobot (0.6.1 and 0.6.2) loads the saved processors of such a model in evaluation mode, so
without this those runs would train with a fixed centre crop, unlike a new run. A new run from
NVIDIA's base model is not changed. The saved model is not changed either: when a model is loaded
to run it (inference), its processors are set to evaluation mode again.
"""

import lerobot.policies.groot.processor_groot as groot_processor
from lerobot.scripts import lerobot_train

_set_mode = groot_processor._set_groot_preprocessor_training


def _training_mode(preprocessor, *, training):
    # lerobot-train builds the processors only to train with them.
    _set_mode(preprocessor, training=True)


groot_processor._set_groot_preprocessor_training = _training_mode

if __name__ == "__main__":
    lerobot_train.main()
