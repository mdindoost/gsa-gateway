from __future__ import annotations
import json
from pathlib import Path
from v2.eval.router.types import Family, LabeledExample

VALID_SKILLS = frozenset({
    "entity_card", "research_of_person", "metric_of_person", "link_of_person",
    "faculty_in_department", "people_in_org", "officers_in_org", "people_by_role",
    "people_by_research_area", "count_people_by_research_area", "areas_in_org", "area_counts",
    "faculty_areas_in_department", "people_by_area_tag", "top_people_by_metric", "org_departments",
    "people_by_name", "person_disambig",
})
VALID_RAG_SOURCES = frozenset({"food", "event", "general"})


def load_dataset(path) -> list[LabeledExample]:
    rows, seen = [], set()
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if not d.get("query"):
            raise ValueError(f"line {n}: missing query")
        if d.get("family") not in Family.ALL:
            raise ValueError(f"line {n}: bad family {d.get('family')!r}")
        if d["id"] in seen:
            raise ValueError(f"line {n}: duplicate id {d['id']!r}")
        seen.add(d["id"])
        if d["family"] == Family.KG and d.get("skill") not in VALID_SKILLS:
            raise ValueError(f"line {n}: KG row needs a known skill, got {d.get('skill')!r}")
        if d["family"] == Family.RAG and d.get("source") not in VALID_RAG_SOURCES:
            raise ValueError(f"line {n}: RAG row needs source in {VALID_RAG_SOURCES}")
        rows.append(LabeledExample(
            id=d["id"], query=d["query"], family=d["family"], skill=d.get("skill"),
            source=d.get("source"), slots=d.get("slots", {}), group=d.get("group")))
    return rows
