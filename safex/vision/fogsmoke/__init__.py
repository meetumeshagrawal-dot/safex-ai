"""Telling fire smoke apart from fog, steam and lens haze.

`classifier.predict()` is the runtime entry point; everything else here exists
to build and train the model behind it. If the model file is missing the
classifier reports itself unavailable and the detection pipeline behaves exactly
as it did before this package existed — a missing model must never be the reason
a fire goes unreported.
"""
from .classifier import predict, available, status, MODEL_PATH   # noqa: F401
