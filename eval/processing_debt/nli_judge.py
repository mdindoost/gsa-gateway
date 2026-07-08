"""NLI cross-encoder entailment judge for the processing-debt presence check.

Mirrors v2/core/retrieval/reranker.py EXACTLY (onnxruntime + `tokenizers`, no torch;
auto-download once to models/nli/; CPU provider; fail-safe -> None). Replaces the granite
generative judge that hedged 'unsure' on unrelated pairs (the proven 2x-inflation bug).

DIRECTION (B4, load-bearing): premise = SPAN (retrieved corpus text), hypothesis = FACT
(the atomic claim). encode_batch pairs are (span, fact). score() returns P(entailment) per
span, softmax over the model's 3 NLI classes; the entail class index is read from config.json
(id2label) so a different NLI export can't silently mis-map. For a PRESENCE check we only care
about entailment vs not: an unrelated span is NEUTRAL (not contradiction), so folding neutral
-> not-present is the caller's job (a low P(entail) covers both neutral and contradiction).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_REPO_ID = "Xenova/nli-deberta-v3-base"
_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "nli"
_MAX_LEN = 512
_SUB_BATCH = 16   # cap rows per forward pass — a large batch at seq-512 blows transformer activation memory


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


class NliJudge:
    def __init__(self, model_dir: Path = _MODEL_DIR, repo_id: str = _REPO_ID,
                 max_len: int = _MAX_LEN, allow_download: bool = True):
        self.model_dir = Path(model_dir)
        self.repo_id = repo_id
        self.max_len = max_len
        self.allow_download = allow_download
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._entail_idx = 1  # default (config 0=contradiction,1=entailment,2=neutral); confirmed at load

    def _model_path(self) -> Path:
        for p in (self.model_dir / "model.onnx", self.model_dir / "onnx" / "model.onnx"):
            if p.exists():
                return p
        return self.model_dir / "onnx" / "model.onnx"

    def _entail_index_from_config(self) -> int:
        cfg = self.model_dir / "config.json"
        try:
            id2label = json.loads(cfg.read_text()).get("id2label", {})
            for idx, label in id2label.items():
                if str(label).lower().startswith("entail"):
                    return int(idx)
        except Exception:  # noqa: BLE001
            pass
        return 1

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
                if not self.allow_download:
                    raise FileNotFoundError(f"NLI model not present at {self.model_dir} (download disabled)")
                self._download()
            tok = Tokenizer.from_file(str(tok_path))
            tok.enable_truncation(max_length=self.max_len)
            tok.enable_padding()
            so = ort.SessionOptions()
            so.intra_op_num_threads = 4  # CPU; GPU is busy with Ollama/embeddings
            sess = ort.InferenceSession(str(self._model_path()), sess_options=so,
                                        providers=["CPUExecutionProvider"])
            self._input_names = {i.name for i in sess.get_inputs()}
            self._entail_idx = self._entail_index_from_config()
            self._tokenizer = tok
            self._session = sess
            logger.info("NLI judge loaded (%s); entail_idx=%d inputs=%s",
                        self.repo_id, self._entail_idx, sorted(self._input_names))

    def _download(self) -> None:
        from huggingface_hub import snapshot_download
        self.model_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=self.repo_id, local_dir=str(self.model_dir),
                          allow_patterns=["onnx/model.onnx", "tokenizer.json", "*.json", "*.txt"])

    def score(self, fact: str, spans: list[str]) -> list[float] | None:
        """P(entailment) that each span entails the fact. premise=span, hypothesis=fact.
        Returns [] for no spans, None on any load/inference failure (fail-safe; caller decides)."""
        if not spans:
            return []
        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            logger.warning("NLI judge unavailable: %s", exc)
            return None
        try:
            out: list[float] = []
            for start in range(0, len(spans), _SUB_BATCH):
                chunk = spans[start:start + _SUB_BATCH]
                encs = self._tokenizer.encode_batch([(s, fact) for s in chunk])  # (premise, hypothesis)
                ids = np.array([e.ids for e in encs], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
                feed = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in self._input_names:
                    feed["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
                logits = np.asarray(self._session.run(None, feed)[0])
                probs = _softmax(logits)
                out.extend(float(p[self._entail_idx]) for p in probs)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("NLI judge scoring failed: %s", exc)
            return None


_SINGLETON: NliJudge | None = None


def get_judge() -> NliJudge:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = NliJudge()
    return _SINGLETON
