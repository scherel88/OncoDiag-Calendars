# OncoDiag Calendars

One public GitHub repository with **two independent publishing branches**. Event research and verification are performed externally by scheduled ChatGPT tasks, not by GitHub Actions.

| Calendar | Working branch | Public subscription feed (kept stable) | Verified events source |
|---|---|---|---|
| Earnings / Financial Calls | [`earnings-financial-calls`](https://github.com/scherel88/OncoDiag-Calendars/tree/earnings-financial-calls) | [`earnings.ics`](https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/earnings.ics) | `earnings-financial-calls/events*.json` and `results/*.json` |
| Scientific & Industry Conferences | [`conferences`](https://github.com/scherel88/OncoDiag-Calendars/tree/conferences) | [`conferences/upcoming-conferences.ics`](https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/conferences/upcoming-conferences.ics) | `conferences:conferences/events.json` |

## How publishing works

1. **External discovery:** The investor monitoring task commits new or corrected source records only to the `earnings-financial-calls` branch; the conference monitor commits only to the `conferences` branch.
2. **Independent GitHub builds:** Each branch contains only its own generator, validator, data, and workflow in `.github/workflows/`. Pushes to source input paths trigger the corresponding build only on that branch.
3. **Independent validation:** Every build validates its source input and the generated iCalendar feed. A failed validation stops publication.
4. **Public mirror:** After successful validation, each workflow copies only its generated `.ics` file into `main`, retrying safely around concurrent updates. Therefore existing subscribers using the previous `main` URLs continue to receive updates.

