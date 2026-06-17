"""Cross-encoder reranker (ONNX) over the fused retrieval pool.

Reorders candidate chunks by joint (query, passage) relevance — fixes the "right doc,
wrong chunk" failures pure RRF/semantic top-K produces. Uses onnxruntime + the `tokenizers`
library only (no torch/transformers). The model auto-downloads once to models/reranker/ and
is cached. Any failure (model missing offline, onnx error) makes score() return None, so the
caller keeps the existing RRF order — reranking is strictly additive.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_REPO_ID = "Xenova/ms-marco-MiniLM-L-6-v2"
_MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "reranker"
_MAX_LEN = 512


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class CrossEncoderReranker:
    def __init__(self, model_dir: Path = _MODEL_DIR, repo_id: str = _REPO_ID,
                 max_len: int = _MAX_LEN):
        self.model_dir = Path(model_dir)
        self.repo_id = repo_id
        self.max_len = max_len
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self.available = True  # flips False after a hard load failure

    def warm(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker warm failed (continuing without rerank): %s", exc)
            return False

    def _model_path(self) -> Path:
        # Xenova repos place the model under onnx/; accept either layout.
        for p in (self.model_dir / "model.onnx", self.model_dir / "onnx" / "model.onnx"):
            if p.exists():
                return p
        return self.model_dir / "onnx" / "model.onnx"

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tok_path = self.model_dir / "tokenizer.json"
            if not self._model_path().exists() or not tok_path.exists():
                self._download()
            tok = Tokenizer.from_file(str(tok_path))
            tok.enable_truncation(max_length=self.max_len)  # passage-side; query is short
            tok.enable_padding()
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2  # shared box also runs Ollama (N1)
            sess = ort.InferenceSession(str(self._model_path()), sess_options=so,
                                        providers=["CPUExecutionProvider"])
            self._input_names = {i.name for i in sess.get_inputs()}
            self._tokenizer = tok
            self._session = sess
            logger.info("reranker loaded (%s); inputs=%s", self.repo_id, sorted(self._input_names))

    def _download(self) -> None:
        from huggingface_hub import snapshot_download
        self.model_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=self.repo_id, local_dir=str(self.model_dir),
                          allow_patterns=["onnx/model.onnx", "tokenizer.json",
                                          "*.json", "vocab.txt"])

    def score(self, query: str, passages: list[str]) -> list[float] | None:
        if not passages:
            return []
        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker unavailable, falling back to RRF: %s", exc)
            self.available = False
            return None
        try:
            # encode_batch pair-encodes (query, passage) AND pads to the longest in the
            # batch — calling encode() per item does NOT pad across calls (ragged arrays).
            encs = self._tokenizer.encode_batch([(query, p) for p in passages])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
            out = np.asarray(self._session.run(None, feed)[0])
            logits = out[:, 1] if (out.ndim == 2 and out.shape[1] == 2) \
                else out.reshape(out.shape[0], -1)[:, 0]
            return [float(s) for s in _sigmoid(logits)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker scoring failed, falling back to RRF: %s", exc)
            return None
