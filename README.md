# Earnings & Financial Calls

The dedicated publishing branch for investor events and financial results. The external ChatGPT daily monitor writes the canonical `events*.json` files and `results/*.json` here. GitHub Actions builds and validates `earnings.ics` independently, then mirrors its verified output to `main/earnings.ics` to preserve existing calendar subscriptions. No event discovery occurs in GitHub.

**Source branch:** `earnings-financial-calls`

**Existing public subscription URL:** https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/earnings.ics

**Direct branch feed:** https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/earnings-financial-calls/earnings.ics

Validation: `python validate_calendar.py && python generate_calendar.py && python validate_calendar.py --ics`.
