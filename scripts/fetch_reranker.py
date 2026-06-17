#!/usr/bin/env python
"""Pre-warm the reranker model (one-time download to models/reranker/). Optional — the bot
auto-downloads on first use; run this to provision ahead of time."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.retrieval.reranker import CrossEncoderReranker

if __name__ == "__main__":
    ok = CrossEncoderReranker().warm()
    print("reranker ready" if ok else "reranker warm FAILED")
    sys.exit(0 if ok else 1)
