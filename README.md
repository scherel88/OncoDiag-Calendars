# Earnings & Financial Calls — working branch

This branch is the canonical home for **validated** earnings calls and investor presentations. It now also holds the **browser extraction engine**, but extracted candidates are never auto-published.

- **Source catalog:** [earnings/config/sources.json](earnings/config/sources.json) — 23 public event pages + 25 corporate news pages; two private companies are news-only.
- **Browser extractor:** [earnings/scripts/extract_public_events.py](earnings/scripts/extract_public_events.py) — shared Chromium, bounded 3-way concurrency, issuer HTML, structured Event JSON, observed public XHR, year selectors, detail links and explicit access errors.
- **Regression tests:** [earnings/tests/test_extractor.py](earnings/tests/test_extractor.py) — purely offline; run before every scheduled browser scan.
- **Daily raw evidence:** [earnings/extractions/](earnings/extractions/) — dated JSON generated at ~7:05 AM Eastern by the minimal default-branch GitHub Actions scheduler. Not a source for ICS generation.
- **ChatGPT task (8:00 AM Eastern):** reviews raw candidates plus all 25 companies' news sources, checks every eligible date/time/webcast on official issuer announcements, deduplicates and commits confirmed entries into **events*.json** only. It also writes reviewed audit findings under earnings/findings/.
- **Publisher:** [.github/workflows/update-calendar.yml](.github/workflows/update-calendar.yml) validates **events*.json**, builds **earnings.ics**, validates independently, and mirrors it to **main/earnings.ics**. A raw extraction commit cannot trigger the publisher.

**Subscriptions:** https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/earnings.ics

**Run locally (Python 3.12):**

\`\`\`bash
pip install -r earnings/requirements-extraction.txt
python -m playwright install --with-deps chromium
python -m unittest discover -s earnings/tests -v
python earnings/scripts/extract_public_events.py --self-test
python earnings/scripts/extract_public_events.py --browser-smoke
python earnings/scripts/extract_public_events.py --output earnings/extractions/manual.json
\`\`\`

The browser scanner never solves CAPTCHAs or bypasses blocked access. A missing event in its output does not mean the issuer has none. ChatGPT must explicitly verify each new event before it enters the published calendar.
