# Issuer source access — diagnostics

The **ChatGPT OncoDiag task** remains the owner of the daily 25-company (75 category) editorial review and calendar updates. This auxiliary GitHub diagnostic does **not** decide whether an investor event is new or publish calendar events.

## Run

Run the [Investor source accessibility audit](https://github.com/scherel88/diagnostics-earnings-calendar/actions/workflows/source-access.yml) GitHub Action manually. It also runs whenever its Python code, source map or workflow changes. Reports are written to `source-probes/YYYY-MM-DD-access.json` (UTC date).

For local execution:

```bash
python -m pip install requests beautifulsoup4 lxml playwright
python -m playwright install chromium
python source_access.py --browser --timeout 12 --retries 2
```

Set the `SEC_USER_AGENT` GitHub Actions secret (or local environment variable) to an identifying organization/application name and a **real administrative contact email** in accordance with the [SEC fair-access policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). Please do not share this contact in a public repository file.

## How it works

1. Loads every configured URL from `source_overrides.json` and visits **every distinct URL**; repeated URLs are fetched once but reported for every corresponding company/category.
2. Detects HTTP errors, 403 restrictions, 429 rate limits, challenge pages, invalid JSON, empty/JavaScript-only pages and readable public content.
3. Uses bounded retries for transient failures, first-party links discovered on other pages for the same issuer, official SEC EDGAR browse alternatives, and Chromium **only for JavaScript-dependent public pages** (not for access-control challenges).
4. Writes a timestamped URL-by-URL log and all 75 category outcomes; outputs `UNRESOLVED` when there is no readable permitted source.
5. The workflow fails its coverage gate when sources are unresolved. A readable URL is **not equivalent** to verified date coverage or a checked calendar event.

No unauthorized access workarounds, forged identity, proxy rotations, CAPTCHA solving or login attempts are implemented. If GitHub's network is blocked, the ChatGPT task must check alternate **official** pages with its own web tools and explicitly report any sources still inaccessible.

**Status labels:** `READABLE` / `READABLE_JS` = content fetched; `ACCESS_RESTRICTED` = 401/403; `RATE_LIMITED` = 429; `JS_OR_THIN_PAGE` = likely JavaScript/empty; `SOURCE_REACHABLE` / `FALLBACK_REACHABLE` = at least one URL with readable content; `UNRESOLVED` = no usable content. None of these imply editorial verification.

Source-check diagnostics are kept separate from `earnings.ics` and cannot delete calendar entries.
