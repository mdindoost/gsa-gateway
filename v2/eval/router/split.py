from __future__ import annotations
import random
import numpy as np
from v2.eval.router.types import LabeledExample


def _cluster_key(examples, encoder, dup_thresh=0.97) -> dict[str, str]:
    """Map example.id -> a cluster id; explicit groups win, else merge near-duplicates."""
    key = {e.id: (e.group or f"_solo_{e.id}") for e in examples}
    ungrouped = [e for e in examples if not e.group]
    if ungrouped:
        mat = encoder([e.query for e in ungrouped])
        sims = mat @ mat.T
        parent = {e.id: e.id for e in ungrouped}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x

        for i in range(len(ungrouped)):
            for j in range(i + 1, len(ungrouped)):
                if sims[i, j] >= dup_thresh:
                    parent[find(ungrouped[j].id)] = find(ungrouped[i].id)
        for e in ungrouped:
            key[e.id] = f"_clu_{find(e.id)}"
    return key


def split(examples, encoder, test_frac=0.3, seed=0):
    key = _cluster_key(examples, encoder)
    clusters: dict[str, list[LabeledExample]] = {}
    for e in examples:
        clusters.setdefault(key[e.id], []).append(e)
    ids = sorted(clusters)
    random.Random(seed).shuffle(ids)
    train, test = [], []
    fam_in_train: set[str] = set()
    n_test_target = int(len(examples) * test_frac)
    for cid in ids:
        members = clusters[cid]
        fams = {m.family for m in members}
        # keep at least one cluster per family in train; otherwise fill test up to target
        if not fams.issubset(fam_in_train) or len(test) >= n_test_target:
            train.extend(members); fam_in_train |= fams
        else:
            test.extend(members)
    return train, test
