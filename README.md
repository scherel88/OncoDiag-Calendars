# Earnings & Financial Calls — working branch

This branch contains verified earnings calls and investor presentations, plus a separate discovery engine. Extracted candidates never enter the calendar automatically.

- **Registry:** [earnings/config/sources.json](earnings/config/sources.json): 23 public companies' investor calendars and all 25 companies' news starting points. DELFI and Element remain ChatGPT-only news research.
- **Extractor:** [earnings/scripts/extract_public_events.py](earnings/scripts/extract_public_events.py): HTTP first, selective Chromium rendering, linked public calendars/feeds and official public-company news fallback.
- **Tests:** [earnings/tests/test_extractor.py](earnings/tests/test_extractor.py): offline parser, cache, retry, rate-limit, fallback and deadline regressions. The browser smoke test also exercises HTTP replay and public-widget recovery without external websites.
- **Evidence:** [earnings/extractions/](earnings/extractions/): daily unverified candidates, source URLs, timing, caching and access diagnostics.
- **ChatGPT verification:** the existing 8:00 AM Eastern task reviews all 48 source categories, corroborates each event, and changes only verified events*.json and official results/*.json. It records findings under earnings/findings/.
- **Publisher:** [.github/workflows/update-calendar.yml](.github/workflows/update-calendar.yml) independently validates, generates earnings.ics and mirrors it to main/earnings.ics. Extraction reports cannot trigger it.

## Discovery and performance

1. A shared HTTP connection pool fetches issuer HTML directly. Event cards, structured Event JSON, linked Google Calendar parameters, ICS and RSS/Atom are parsed before launching a browser. Publication timestamps never stand in for event times.
2. ETags and Last-Modified revalidate cached bodies. Identical concurrent requests share one result. Network failures do not reuse stale bodies as fresh evidence. The optional cache is bounded to 200 files / 32 MB of recent data and excludes no-store responses.
3. Real linked event-detail pages can supply missing facts even when the listing parser returns zero candidates. A blocked/empty calendar also gets bounded public issuer-news fallback; private companies are excluded from the script.
4. Chromium is reserved for dynamic/empty sources. Successfully fetched public HTML and scripts are served under their original issuer URLs; stylesheets, images, fonts, media and selected analytics scripts are skipped. Critical published scripts are prefetched. The browser captures JSON/JSONP responses actually made by the issuer's own widget.
5. If a Q4 page's bootstrap is broken, the already-loaded issuer library can initialize its event widget with forcePublic=true. URLs and parameters come from that published library, and observed requests are recorded. No private service URLs or API keys are invented.
6. Six companies can progress together, with at most two browser contexts and two simultaneous HTTP requests per host. Retry-After cools a rate-limited host; transient server/network failures get one retry. A company's entire scan has one deadline, and partial candidates survive it.

A readable page, a candidate and a verified event are separate states. Every report retains per-source errors, browser errors, conflicting timestamps and missing fields. Explicit EST/EDT mistakes and contradictory displayed clocks remain pending. Generic PDF attachment labels are not separate events.

## Run locally

Python 3.12:

~~~bash
python -m pip install -r earnings/requirements-extraction.txt
python -m playwright install --with-deps chromium
python -m unittest discover -s earnings/tests -v
python earnings/scripts/extract_public_events.py --self-test
python earnings/scripts/extract_public_events.py --browser-smoke
python earnings/scripts/extract_public_events.py \
  --cache-dir .cache/investor-events \
  --concurrency 6 --browser-concurrency 2 \
  --output earnings/extractions/local.json
~~~

Use --company repeatedly for focused diagnosis, or --http-only to disable optional rendering. The default-branch scheduler runs around 7:05 AM Eastern and saves the dated report to this branch. Its Actions cache persists revalidatable responses separately from Git.

No CAPTCHA solving, proxy rotation, authenticated crawling, private endpoints or SEC scans are used. MGI's unconfirmed event-calendar status remains explicit. Zero candidates never means no events.

Subscription: https://raw.githubusercontent.com/scherel88/OncoDiag-Calendars/main/earnings.ics

