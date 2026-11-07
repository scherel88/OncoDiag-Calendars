# OncoDiag Calendars

**Two subscription calendars, one repository, independent publishing branches, with ChatGPT as the final fact-checker.** Public issuer event websites sometimes render events in JavaScript or block browsers. The earnings pipeline therefore has an automated HTTP-first **discovery** stage with selective browser recovery. Discovery output is **not** trusted calendar content until ChatGPT corroborates it.

## Subscribe

| Calendar | Published feed (stable URL) | Working branch |
| --- | --- | --- |
| Earnings calls & investor presentations | [earnings.ics](https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/earnings.ics) | [earnings-financial-calls](https://github.com/scherel88/OncoDiag-Calendars/tree/earnings-financial-calls) |
| Scientific & investor conferences | [conferences.ics](https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/conferences.ics) | [conferences](https://github.com/scherel88/OncoDiag-Calendars/tree/conferences) |

**Source-of-truth rule:** Never edit a published ICS feed manually. ChatGPT edits verified JSON in the appropriate working branch; that branch's independent GitHub builder validates and mirrors the ICS to main.

## Architecture at a glance

### 1. Earnings / Financial Calls — HTTP + selective browser + ChatGPT

**A. GitHub Actions discovers public investor-event CANDIDATES at ~7:05 AM ET daily.**

- Main-branch scheduler: [.github/workflows/extract-investor-events.yml](.github/workflows/extract-investor-events.yml). GitHub scheduled workflows must exist on the default branch; this small launcher checks out the earnings working branch.
- Actual extractor: [earnings/scripts/extract_public_events.py](https://github.com/scherel88/OncoDiag-Calendars/blob/earnings-financial-calls/earnings/scripts/extract_public_events.py), **only on earnings-financial-calls**.
- Registry: [earnings/config/sources.json](https://github.com/scherel88/OncoDiag-Calendars/blob/earnings-financial-calls/earnings/config/sources.json). Contains **23 public company event calendars and 25 issuer news-release archives** (23 public + 2 private).
- A shared HTTP connection pool reads issuer HTML, structured Event JSON, linked calendar parameters, public ICS/RSS/Atom, real event-detail links, and public issuer announcements as a fallback. Raw news publication timestamps never become call times.
- ETag/Last-Modified revalidation and coalesced identical requests reduce duplicate work. A bounded Actions cache persists revalidatable bodies; failed requests never pass off stale cached content as a fresh check.
- Chromium is reserved for dynamic/empty pages. It replays readable public HTML/scripts under the issuer's original URLs, prefetches critical scripts, skips stylesheets/media and selected analytics, and captures the issuer widget's actual public JSON/JSONP responses.
- Broken Q4 widget initialization can be retried through the issuer's already-loaded published library with **forcePublic=true**. No private endpoint or API key is guessed. Exact observed request URLs and failures are retained.
- Six companies can progress together, with at most two browser contexts and two simultaneous HTTP requests per host. Retry-After cools rate-limited hosts; transient failures get one retry. Partial candidates survive the single end-to-end company deadline.
- Numeric dates, different event-card templates, timezone conflicts, and PDF attachment labels have dedicated regression coverage. All conflicting or incomplete event facts remain subject to ChatGPT review.
- Raw findings: [earnings/extractions/YYYY-MM-DD.json](https://github.com/scherel88/OncoDiag-Calendars/tree/earnings-financial-calls/earnings/extractions). This is a **candidate queue**. It is not read by the calendar generator, and saving it cannot create an ICS event. Reports distinguish browser errors, zero extracted candidates, missing timestamps and links. Zero extracted is **not** proof of zero events.
- No proxy rotation, CAPTCHA-solving, authenticated crawling, private Q4 endpoints or SEC/EDGAR filing scans. If GitHub's network is blocked, the source stays incomplete for ChatGPT to investigate.

**B. ChatGPT reviews every issuer at 8:00 AM ET daily.**

- Existing scheduled task: **OncoDiag: All Finance Calls** (not a duplicate task).
- Read the newest GitHub extraction report and the existing earnings event records/ICS. For all public companies, review both their investor event calendar **and** official news releases (23 × 2 = 46 checks). For private **DELFI Diagnostics** and **Element Biosciences**, review **news/press releases only** (2 checks); total **48**.
- Corroborate candidate dates/times, issuer attendance, time zones, and official webcast or event-detail URLs using the issuer event listing **plus dated issuer releases or event-specific issuer pages**. The 48 curated official starting-point URLs supply more than 30 distinct research websites; additionally search current issuer websites when URLs move. Search indexed issuer event-detail pages for blocked/dynamic records, but never call a blocked homepage "fully verified".
- Reject guesses, duplicates (including aliases such as "Caris" vs "Caris Life Sciences"), poster-only conferences, scientific webinars or events with unresolved date/time/webcast. Incomplete candidates remain pending.
- ChatGPT commits **verified** data into the earnings branch's existing canonical **events*.json**, and financial summaries only from official company releases into **results/*.json** where appropriate. A finding is recorded under **earnings/findings/YYYY-MM-DD.json** even when nothing changed. No GitHub crawler writes validated event records.

**C. GitHub ONLY validates and publishes confirmed earnings JSON.**

- [Earnings publisher](https://github.com/scherel88/OncoDiag-Calendars/blob/earnings-financial-calls/.github/workflows/update-calendar.yml) runs after a change to canonical earnings inputs, validates source records, builds earnings.ics, validates the full ICS independently, and mirrors **earnings.ics** into **main** with bounded retry on concurrent updates.
- The extractor commits only to earnings/extractions/, a path that **does not trigger the earnings publisher**. The publisher **never** promotes raw candidate data.
- ChatGPT must verify stable UIDs, full historical preservation, no duplicates, correct one-hour duration, branch build status and the main public feed before reporting publication as successful. If an Actions run or feed cannot be verified, report PARTIAL.

### 2. Conferences — ChatGPT research only

- Existing scheduled task: **OncoDiag: All Conferences** (weekly on Monday, 10:00 AM ET).
- ChatGPT works from the [45-website research registry](https://github.com/scherel88/OncoDiag-Calendars/blob/conferences/conferences/source_registry.json), spanning scientific organizers, investor-conference hosts and independent cross-checks. Inspect **at least 30 distinct sites each full weekly sweep**, prioritize all tracked conference organizer websites, then research specific future editions. Do not mistake a current year's conference for a future edition.
- Each edition needs an **explicit** published date range, verified venue or a clearly marked unannounced venue, and a primary organizer/venue source. Independently corroborate from a second reliable source where available; a news aggregator is never the only proof. No extrapolated annual dates.
- ChatGPT alone updates [conferences/events.json](https://github.com/scherel88/OncoDiag-Calendars/blob/conferences/conferences/events.json) on the **conferences** branch. No GitHub conference crawler or external automated event discovery exists.
- [Conference publisher](https://github.com/scherel88/OncoDiag-Calendars/blob/conferences/.github/workflows/build-conferences.yml) runs static source-catalog/schema validation, generates and independently validates the conference ICS, then mirrors **conferences.ics** to main. The organizer registry is validated locally by **conferences/validate_sources.py**; it never visits the websites.
- Keep stable edition UIDs and existing verified editions. The calendar title is the conference's plain published title—do not append "(2027)" automatically.

## Responsibilities and validation

| Stage | GitHub discovery job | ChatGPT task | Publishing workflow |
| --- | --- | --- | --- |
| Render dynamic public investor calendars | Yes, earnings only | Can cross-check via web/search | No |
| Read public issuer releases | Bounded fallback only | Yes, every issuer daily | No |
| Research private companies | No | Yes, news only | No |
| Discover and corroborate conferences | No | Yes, weekly, 45 sources | No |
| Verify exact event facts and deduplicate | Candidates only | **Yes, required** | Structural validation only |
| Change canonical event JSON | **Never** | **Yes, correct branch only** | Never |
| Generate and publish ICS | Never | Checks published result | **Yes, independently** |
| Publish audit/error findings | Raw extraction JSON | Reviewed findings JSON | Build/validation logs |

**Failure rule:** If an event page returns a JavaScript shell, CAPTCHA, 403, an empty page, or a partial archive, record exactly what was observed. A successful request or a search snippet does not establish a complete event review. Never report 48/48 verified unless all 48 categories were substantively checked. Never silently drop historical calendar entries.

## Running and troubleshooting

From the **earnings-financial-calls** branch, with Python 3.12:

```bash
python -m pip install -r earnings/requirements-extraction.txt
python -m playwright install --with-deps chromium
python -m unittest discover -s earnings/tests -p 'test_*.py' -v
python earnings/scripts/extract_public_events.py --self-test
python earnings/scripts/extract_public_events.py --browser-smoke
python earnings/scripts/extract_public_events.py \
  --config earnings/config/sources.json \
  --output earnings/extractions/local-test.json \
  --cache-dir .cache/investor-events \
  --concurrency 6 --browser-concurrency 2 --site-deadline 130
```

Or open [GitHub Actions → Extract public investor-event candidates](https://github.com/scherel88/OncoDiag-Calendars/actions/workflows/extract-investor-events.yml) and choose **Run workflow**. The scheduled workflow runs at 07:05 America/New_York using two UTC cron expressions and an Eastern-time gate; GitHub scheduled start times may be delayed.

If extraction fails: read the job log and saved Actions artifact; check whether Chromium was installed, the source URL moved, a site challenged automated visitors, or no events were found. The saved report is evidence for **ChatGPT** to investigate, not permission to publish inferred events.

If earnings publication fails: inspect the earnings-branch publisher's test, generation and mirroring steps, then confirm [main/earnings.ics](https://github.com/scherel88/OncoDiag-Calendars/blob/main/earnings.ics). If conference publication fails: do the equivalent on its independent branch/workflow. Avoid unrelated branch changes and force pushes.

### Earlier subscription URLs

- The repository was renamed from diagnostics-earnings-calendar to **OncoDiag-Calendars**; use the current URLs above.
- The retired conference location **main/conferences/upcoming-conferences.ics** is no longer produced. The current feed is **main/conferences.ics**.
