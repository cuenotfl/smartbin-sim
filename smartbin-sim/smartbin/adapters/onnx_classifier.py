"""Real classifier adapter (ADR-002): ONNX model, runs on a laptop CPU now and
on the Jetson later (same .onnx, optionally converted to TensorRT).

Pipes and Filters: decode -> resize/normalise -> inference -> softmax -> decision.
Optional dependencies: onnxruntime, numpy, pillow (requirements-ml.txt).
"""
import io
from typing import Any, Optional, Sequence

from ..core.model import Classification, Fraction
from ..core.ports import ClassifierPort

MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


class OnnxClassifier(ClassifierPort):
    def __init__(self, model_path: str, labels: Sequence[str] = ("ALU", "PET", "REST"),
                 input_size: int = 224, session: Optional[Any] = None) -> None:
        """`labels` must be in the order of the model's output neurons.
        `session` can be injected for tests."""
        self.labels = [Fraction(label) for label in labels]
        self.input_size = input_size
        if session is None:
            import onnxruntime as ort
            session = ort.InferenceSession(model_path, providers=ort.get_available_providers())
        self.session = session
        self.input_name = session.get_inputs()[0].name

    def preprocess(self, image: bytes):
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image)).convert("RGB")
        img = img.resize((self.input_size, self.input_size))
        x = np.asarray(img, dtype=np.float32) / 255.0
        x = (x - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
        return x.transpose(2, 0, 1)[None, ...]  # NCHW

    def classify(self, image: bytes) -> Classification:
        import numpy as np
        logits = self.session.run(None, {self.input_name: self.preprocess(image)})[0][0]
        e = np.exp(logits - np.max(logits))
        probs = e / e.sum()
        i = int(np.argmax(probs))
        return Classification(self.labels[i], float(probs[i]))
