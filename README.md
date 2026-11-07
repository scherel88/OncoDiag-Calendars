# Scientific & Industry Conferences — working branch

**Conference discovery is entirely ChatGPT-owned; GitHub runs no conference scraper.** The OncoDiag: All Conferences task searches each announced future edition and publishes verified additions/changes to **conferences/events.json**.

- **Research sources:** [conferences/source_registry.json](conferences/source_registry.json): 45 distinct organizer, investor-conference and corroboration websites. Every full weekly sweep reviews at least 30 distinct sources, with edition-specific official organizer/venue evidence.
- **Schema-only checker:** [conferences/validate_sources.py](conferences/validate_sources.py). It checks the research catalog without opening the Internet or discovering events.
- **Canonical conference editions:** [conferences/events.json](conferences/events.json) (exclusive end date, stable edition UID, exact venue where known). ChatGPT edits this file and preserves validated historical editions.
- **Builder:** [.github/workflows/build-conferences.yml](.github/workflows/build-conferences.yml) independently validates the JSON and ICS, then publishes to main.

**Subscription:** https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/conferences.ics

**Run validations (Python 3.12):**

\`\`\`bash
python conferences/validate_sources.py
python conferences/validate_calendar.py
python conferences/generate_calendar.py
python conferences/validate_calendar.py --ics
\`\`\`

Never invent future dates or venues, never add an automatic year suffix to conference titles, and never edit the published ICS by hand.
