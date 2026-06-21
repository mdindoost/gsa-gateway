"""Blind-labeling protocol helpers for building the GOLD/TEST set.

The gold test set must be labeled BLIND (human-first, without seeing the LLM's proposed route) to
keep the proposer's prior from silently becoming the test answer. Flow:
  1. make_blind_stubs(real questions) -> family:"?" stubs the human labels independently.
  2. inject_canaries(...) plants deliberate errors into a review batch to audit the reviewer.
  3. merge_blind_labels(human labels, LLM proposals) -> final rows, recording proposed_family +
     whether the independent human label matched (confirmed), for κ / edit-rate auditing.
"""
from __future__ import annotations
import math
import random


def make_blind_stubs(queries, start_id: int = 0, split: str = "test") -> list[dict]:
    return [{"id": f"t{start_id + i}", "query": q, "family": "?",
             "provenance": "real", "split": split}
            for i, q in enumerate(queries)]


def merge_blind_labels(human_rows, proposals: dict, annotator: str | None = None,
                       split: str = "test") -> list[dict]:
    """Attach the LLM's proposed_family + a `confirmed` flag (did the independent human label match?)
    to each human-labeled row, stamping provenance/split/annotator for the gold set."""
    out = []
    for r in human_rows:
        prop = proposals.get(r["id"])
        row = dict(r)
        row["provenance"] = "real"
        row["split"] = split
        if annotator is not None:
            row["annotator"] = annotator
        if prop is not None:
            row["proposed_family"] = prop
            row["confirmed"] = (r["family"] == prop)
        out.append(row)
    return out


def inject_canaries(proposals: dict, frac: float, seed: int, families) -> tuple[dict, set]:
    """Corrupt a fraction of proposals to a DIFFERENT family (planted errors). If the reviewer's
    correction rate on these isn't ~100%, the review is rubber-stamping. Returns (corrupted, ids)."""
    ids = sorted(proposals)
    rng = random.Random(seed)
    rng.shuffle(ids)
    k = max(1, math.ceil(len(ids) * frac))
    canary = set(ids[:k])
    out = dict(proposals)
    for i in canary:
        alts = [f for f in families if f != proposals[i]]
        if alts:
            out[i] = rng.choice(alts)
    return out, canary
