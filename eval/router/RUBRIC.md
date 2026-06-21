# Router labeling rubric (Kavosh v2.1) — shared by the expert panel

Label each user question with its correct ROUTE: a `family`, and for KG a `skill`, for RAG a `source`.
Output one label per question. Format: `KG/<skill>`, `RAG/<source>` (append ` +live` where noted),
or just the family for CLARIFY / COMMAND / OTHER. If a question is context-dependent (see R6) use `hardneg`.

## Families
- **KG** — a precise structured fact about a specific person / org / role / metric / research-area / department, answerable from a knowledge graph.
- **RAG** — a prose / explanatory answer from documents (policies, "what is X", how-to, programs, offices, event info).
- **CLARIFY** — under-specified; cannot route without more info (missing which org/person; "top N" with no metric).
- **COMMAND** — a control action, not a question (clear / reset / help / start / qrcode / judge mode).
- **OTHER** — social, meta-about-the-assistant, out-of-scope, non-English, complaint/feedback, or a bare token with no intent.

**KG vs RAG:** a specific entity/list/number/role/department from the graph = KG; explanatory document text = RAG.

## KG skills
`entity_card` (who is X / X info / tell me about X / "how is X") · `research_of_person` (X's research field/area) ·
`metric_of_person` (X's citations/h-index) · `link_of_person` (X's scholar/linkedin/website) ·
`faculty_in_department` (who's in dept Y's faculty) · `people_in_org` (who is in org Y) · `officers_in_org` ·
`people_by_role` (who is the provost/dean/chair; "is X the provost?") · `people_by_research_area` (who works on Z) ·
`count_people_by_research_area` · `areas_in_org` · `faculty_areas_in_department` · `top_people_by_metric` (rank/most/top-N by a metric) ·
`org_departments` (what departments/colleges are in Y) · `people_by_name` (list/has people with a given name)

## RAG sources
`food` · `event` · `general`.  Append ` +live` for NJIT logistics answered from the live njit.edu site
(parking, library/gym/clinic hours, tuition, courses, academic calendar, I-20/CPT/OPT process).

## Established rules (from prior adjudication — apply consistently)
- **R1** about-the-assistant ("what is Kavosh", "what is your version", "your llm engine", "tell me about GSA Gateway") → **OTHER**.
- **R2** non-English query → **OTHER**.
- **R3** a user *statement / correction / complaint / feedback* ("Wrong", "Ur bot is biased", "I think X is a lecturer") → **OTHER**.
- **R4** "search X" → classify by the UNDERLYING intent (search for a person → KG; search parking → RAG). Append `+live` for njit logistics.
- **R5** "top-N / most / ranking" WITH a metric (citations / h-index) → **KG/top_people_by_metric**; WITHOUT a metric ("top 10 in X") → **CLARIFY**.
- **R6** context-dependent follow-up / bare token / pronoun with no antecedent ("Yes", "more", "what about X?", "how can I get it", "why didn't you list him", a bare number) → **hardneg** (context-dependent suite; not a headline test family).
- **R7** obvious typos → label the INTENDED word ("judge mide" → judge mode → COMMAND; "Prtras" → Petras).
- **R8** event-people (MMI organizers / speakers) → **RAG**, NOT KG — the KG models people/orgs/research, not event rosters.
- **R9** role lookup ("who is the provost/dean/chair", "is X the provost") → **KG/people_by_role** (resolver must EXACT-match the role; many "vice/associate provost" titles exist).
- **R10** `+live` is a sub-tag on RAG for live-site logistics — it is not its own family.
- **R11** identity → `entity_card`; X's research → `research_of_person`; X's citations/h-index → `metric_of_person`; X's links → `link_of_person`; a bare person name → `entity_card`.
- **R12** "who's in dept Y / faculty of Y" → `faculty_in_department`; "who is in org Y" → `people_in_org`; "officers of Y" → `officers_in_org`; "what departments/colleges in Y" → `org_departments`; "list/has people named Z" → `people_by_name`.

When a question doesn't fit cleanly, note WHY and which rule is ambiguous — those notes drive the Round-2 debate.
