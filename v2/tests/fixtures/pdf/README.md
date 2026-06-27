# PDF Test Fixtures

Real NJIT PDFs used for integration testing of `pdf_extract.py`.
Fetched 2026-06-27 with `curl -sL -A "GSA-Gateway-Bot/2.0 (+https://gsanjit.com)"`.

| File | Source URL | sha256 (first 16 hex) | Size |
|------|------------|----------------------|------|
| calendar.pdf | https://catalog.njit.edu/about-university/academic-calendar/academic-calendar.pdf | 54f2b29a2a89f2f5 | 18,933 bytes |
| tuition.pdf  | https://catalog.njit.edu/undergraduate/admissions-financial-aid/tuition-fees/tuition-fees.pdf | 1ea010ca22d3ce3c | 20,058 bytes |

## Notes

- `calendar.pdf` — 2-page prose PDF (NJIT Academic Calendar 2025–2026). Tests clean text
  extraction and whitespace normalization. Expected: `status="ok"`, `table_degraded=False`.
- `tuition.pdf` — 2-page dense monetary-table PDF (NJIT Tuition & Fees 2025–2026). Tests
  the table-degraded heuristic: after whitespace normalization, adjacent column values collapse
  to consecutive monetary strings (e.g. `"660.00 279.00 939.00"`). Expected: `status="ok"`,
  `table_degraded=True`.

Do not re-fetch automatically; treat these as stable fixtures.
To verify: `python3 -c "import hashlib; print(hashlib.sha256(open('v2/tests/fixtures/pdf/calendar.pdf','rb').read()).hexdigest()[:16])"`
