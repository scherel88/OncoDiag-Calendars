# OncoDiag Calendars

One public GitHub repository with **two independent publishing branches**. Event discovery and verification run externally through scheduled ChatGPT tasks; GitHub Actions only validates, generates, and publishes calendar feeds.

| Calendar | Working branch | Public subscription feed | Source data |
| --- | --- | --- | --- |
| Earnings / Financial Calls | [`earnings-financial-calls`](https://github.com/scherel88/OncoDiag-Calendars/tree/earnings-financial-calls) | [`earnings.ics`](https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/earnings.ics) | `events*.json` and `results/*.json` in the earnings branch |
| Scientific & Industry Conferences | [`conferences`](https://github.com/scherel88/OncoDiag-Calendars/tree/conferences) | [`conferences.ics`](https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/conferences.ics) | `conferences/events.json` in the conferences branch |

## Workflow

1. Each external monitoring task researches official sources and commits verified JSON updates **only to its dedicated working branch**.
2. Each working branch has a separate GitHub Actions workflow, generator, and validator. A push to relevant inputs triggers the correct build and validates the source and generated ICS.
3. After successful validation, each workflow mirrors its verified ICS feed to the **root of `main`**, with retries for concurrent pushes.
4. `main` is publication-only: this README and the two published `.ics` feeds. **Do not edit generated ICS files directly.**

**Subscription change:** The previous conference feed `main/conferences/upcoming-conferences.ics` has moved to `main/conferences.ics`. Subscribers to the old path must replace their subscription URL with the new one. The earnings URL is unchanged.
