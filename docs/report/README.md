# Regenerate the technical report PDF

Source: `docs/report/gsa_gateway_report.html` + `style.css`

```bash
google-chrome --headless --disable-gpu --no-sandbox \
  --print-to-pdf=docs/GSA_Gateway_Technical_Report.pdf --no-pdf-header-footer \
  "file://$PWD/docs/report/gsa_gateway_report.html"
```

All figures were verified against the live DB and source on 2026-08-22 by a 4-agent
fact-check pass (29 findings applied). Re-verify before republishing.
