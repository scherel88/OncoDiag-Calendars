# Scientific & Industry Conferences

The dedicated publishing branch for conferences. The external ChatGPT weekly monitor writes verified editions to `conferences/events.json` here. GitHub Actions builds and independently validates `conferences/upcoming-conferences.ics`, then mirrors its verified output to `main/conferences/upcoming-conferences.ics` to preserve existing subscriptions. No event discovery occurs in GitHub.

**Source branch:** `conferences`

**Existing public subscription URL:** https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/conferences/upcoming-conferences.ics

**Direct branch feed:** https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/conferences/conferences/upcoming-conferences.ics

Validation: `python conferences/validate_calendar.py && python conferences/generate_calendar.py && python conferences/validate_calendar.py --ics`.
