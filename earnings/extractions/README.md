# Unverified investor-event extraction reports

At approximately 07:05 America/New_York each day, the main-branch Chromium launcher checks out `earnings-financial-calls`, executes `earnings/scripts/extract_public_events.py`, and writes `YYYY-MM-DD.json` here. The report lists every attempted public company, the URL actually opened, browser errors, observed events and any missing details. It is **not** an exhaustive source-coverage certificate and **must never** be imported directly into `events*.json` or `earnings.ics`.

Only ChatGPT's daily verification task may promote independently confirmed issuer events into canonical event JSON. Empty/blocked scans must remain explicitly incomplete.
