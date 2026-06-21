from __future__ import annotations
import math
import random
import numpy as np
from v2.eval.router.types import LabeledExample

_ENTITY_KEYS = ("org", "person", "area")


def entity_of(ex: LabeledExample) -> str | None:
    """Canonical entity an example is ABOUT (org/person/area), or None for entity-less rows.

    Used by the entity-disjoint split: holding out whole entities is the only way to measure
    whether the router generalizes past the dominating entity token ("org token dominates").
    """
    s = ex.slots or {}
    for k in _ENTITY_KEYS:
        v = s.get(k)
        if v:
            return f"{k}:{str(v).strip().lower()}"
    return None


def _stratum_of(ex: LabeledExample):
    """The class the split must keep in train: (KG, skill) / (RAG, source) / (family, None)."""
    if ex.family == "KG":
        return ("KG", ex.skill)
    if ex.family == "RAG":
        return ("RAG", ex.source)
    return (ex.family, None)


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
    """Paraphrase-disjoint split, stratified by (family, skill/source).

    Keeps >=1 cluster per STRATUM in train (not just per family), so a single-group skill stays in
    train rather than landing in test with no same-skill exemplar (which would make its accuracy a
    measurement artifact, per the bake-off review).
    """
    key = _cluster_key(examples, encoder)
    clusters: dict[str, list[LabeledExample]] = {}
    for e in examples:
        clusters.setdefault(key[e.id], []).append(e)
    ids = sorted(clusters)
    random.Random(seed).shuffle(ids)
    train, test = [], []
    strata_in_train: set = set()
    n_test_target = int(len(examples) * test_frac)
    for cid in ids:
        members = clusters[cid]
        strata = {_stratum_of(m) for m in members}
        # keep at least one cluster per stratum in train; otherwise fill test up to the target
        if not strata.issubset(strata_in_train) or len(test) >= n_test_target:
            train.extend(members); strata_in_train |= strata
        else:
            test.extend(members)
    return train, test


def split_entity_disjoint(examples, test_frac=0.3, seed=0):
    """Entity-disjoint split: hold out whole entities (orgs/people/areas) for test.

    This is the PRIMARY honesty metric for the bake-off — it's the only split that can detect a
    classifier cheating on the dominating entity token, because the test entities were never seen in
    training. Entity-less rows (RAG/general, OTHER, COMMAND, CLARIFY) carry no entity to hold out and
    go to train; the test set is exactly the rows about held-out entities.
    """
    entities = sorted({e for e in (entity_of(x) for x in examples) if e})
    if not entities:
        return list(examples), []
    rng = random.Random(seed)
    rng.shuffle(entities)
    n_hold = max(1, math.ceil(len(entities) * test_frac))
    held_out = set(entities[:n_hold])
    train, test = [], []
    for x in examples:
        ent = entity_of(x)
        if ent is not None and ent in held_out:
            test.append(x)
        else:
            train.append(x)
    return train, test
