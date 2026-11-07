# Published calendars

This repository has exactly two GitHub Actions publishing pipelines. **Discovery and source verification run externally** (the ChatGPT investor-events and conference monitoring tasks); GitHub only validates supplied data, builds ICS, and publishes it.

| Calendar | Subscription link | External inputs | GitHub builder/validator |
| --- | --- | --- | --- |
| Investor events and earnings | https://raw.githubusercontent.com/scherel88/diagnostics-earnings-calendar/main/earnings.ics | Root events*.json and results/*.json | generate_calendar.py and validate_calendar.py |
| Scientific and industry conferences | https://raw.githubusercontent.com/scherel88/diagnostics-earnings-calendar/main/conferences/upcoming-conferences.ics | conferences/events.json | conferences/generate_calendar.py and conferences/validate_calendar.py |

## Source updates

**Investor events:** The external daily monitor appends or updates official event records in root events*.json and official results in results/*.json. Editing either input triggers the investor build and independent ICS validation; a results-only change also triggers a build.

**Conferences:** The external weekly monitor updates conferences/events.json from verified organizer announcements. Its object has schema_version: 1 and an events list. Each event needs uid, title, start_date, end_date (exclusive, YYYY-MM-DD), location, url, description. Preserve an edition's UID when it is rescheduled. Missing venues should be recorded as "Venue to be announced" rather than invented.

Do not directly edit either generated ICS file as the source of truth. Existing subscribed ICS URLs and original conference UIDs are preserved. The separate GitHub workflows run after external JSON updates or via manual Actions dispatch. Neither workflow scrapes event websites.

## Local validation

Run from repository root:

    python validate_calendar.py
    python generate_calendar.py
    python validate_calendar.py --ics

    python conferences/validate_calendar.py
    python conferences/generate_calendar.py
    python conferences/validate_calendar.py --ics

Old source-probing and audit-execution scripts/workflows were removed. Historical audit/source-probe JSON evidence is retained but does not participate in these publishing pipelines.
