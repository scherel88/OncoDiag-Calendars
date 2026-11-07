#!/usr/bin/env python3
"""Check every configured issuer URL and discover permitted, first-party fallbacks.

This is an ACCESS AUDIT, not proof of seven-day event coverage or calendar sync.
No proxy rotation, CAPTCHA solving, account login, or access-control evasion.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import time
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent
CATEGORIES = (
    "official_ir_events_webcasts",
    "corporate_ir_press_releases_investor_conferences",
    "official_filings_financial_reports",
)
KEYWORDS = {
    CATEGORIES[0]: ("event", "webcast", "presentation", "investor", "calendar"),
    CATEGORIES[1]: ("news", "release", "press", "investor", "announcement"),
    CATEGORIES[2]: ("filing", "sec", "report", "financial", "investor", "disclosure"),
}
BAD_CONTENT = (
    "access denied", "verify you are human", "enable javascript and cookies",
    "checking your browser", "attention required! | cloudflare", "captcha challenge",
)
MAX_BYTES = 1_000_000
DATE_ITEM = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}|\b\d{1,2}/\d{1,2}/20\d{2}\b", re.I)
CALENDAR_PATHS = ("events", "press-releases", "news-releases", "sec-filings", "ir-calendar")


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def host(url):
    return (urlparse(url).hostname or "").lower()


def normalize(url):
    p = urlparse(url)
    return p._replace(fragment="").geturl()


def candidate_link(href, base):
    try:
        u = normalize(urljoin(base, href))
        if urlparse(u).scheme not in ("http", "https"):
            return None
        return u
    except (TypeError, ValueError):
        return None


def relevant(url, text, category):
    hay = (urlparse(url).path + " " + text[:200]).lower()
    return any(word in hay for word in KEYWORDS[category])


def inspect(response, category=None):
    raw = response.content[:MAX_BYTES]
    ctype = response.headers.get("Content-Type", "").lower()
    blob = raw.decode(response.encoding or "utf-8", errors="replace")
    links = []
    if "json" in ctype or (blob.lstrip().startswith("{") and "html" not in ctype):
        try:
            data = json.loads(blob)
            # SEC submissions JSON: validate its schema rather than trusting HTTP 200.
            valid = isinstance(data, dict) and (
                "filings" in data or "cik" in data or "accessionNumber" in data
            )
            status = "READABLE" if valid else ("READABLE" if len(blob) > 200 else "THIN")
            title = data.get("name", "JSON response") if isinstance(data, dict) else "JSON"
            return status, str(title), len(blob), links
        except ValueError:
            return "INVALID_JSON", "", 0, links
    if "xml" in ctype or blob.lstrip().startswith(("<?xml", "<rss", "<feed", "<urlset")):
        soup = BeautifulSoup(blob, "xml")
        for el in soup.find_all(["loc", "link"], limit=100):
            raw_url = el.get("href") or el.get_text(" ", strip=True)
            u = candidate_link(raw_url, response.url)
            if u:
                links.append((u, el.get_text(" ", strip=True)[:120]))
        title = soup.title.get_text(" ", strip=True) if soup.title else "XML feed/index"
        return ("READABLE" if len(blob) >= 100 else "THIN"), title, len(blob), links
    soup = BeautifulSoup(blob, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True)[:140] if soup.title else ""
    main = soup.main or soup.body or soup
    text = main.get_text(" ", strip=True)
    lower = (title + " " + text[:2500]).lower()
    for needle in BAD_CONTENT:
        if needle in lower:
            return "BLOCKED_PAGE", title, len(text), links
    for a in soup.find_all("a", href=True, limit=250):
        u = candidate_link(a["href"], response.url)
        if u:
            links.append((u, a.get_text(" ", strip=True)[:120]))
    for a in soup.find_all("link", href=True, limit=80):
        if any(x in str(a.get("type", "")).lower() for x in ("rss", "atom", "xml")):
            u = candidate_link(a["href"], response.url)
            if u:
                links.append((u, "syndication feed"))
    if len(text) < 140 or (any(k in urlparse(response.url).path.lower() for k in CALENDAR_PATHS) and not DATE_ITEM.search(text)):
        return "JS_OR_THIN_PAGE", title, len(text), links
    return "READABLE", title, len(text), links


class Auditor:
    def __init__(self, retries=2, browser=False, timeout=16):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (os.getenv("SEC_USER_AGENT") or "OncoDiag investor calendar monitor; contact: https://github.com/scherel88/diagnostics-earnings-calendar"),
            "Accept": "text/html,application/json,application/xml,application/rss+xml;q=0.9,*/*;q=0.6",
        })
        self.retries = retries
        self.browser = browser
        self.timeout = timeout
        self.cache = {}
        self.browser_jobs = 0

    def attempt(self, url):
        attempts = []
        for number in range(1, self.retries + 1):
            a = {"at": stamp(), "attempt": number, "url": url}
            try:
                if host(url) == "data.sec.gov":
                    time.sleep(0.20)  # Respect SEC's fair-access rate requirements.
                with self.session.get(url, timeout=self.timeout, allow_redirects=True) as response:
                    a["http_status"] = response.status_code
                    a["final_url"] = response.url
                    if response.status_code in (401, 403):
                        a["status"] = "ACCESS_RESTRICTED"
                    elif response.status_code == 429:
                        a["status"] = "RATE_LIMITED"
                    elif response.status_code >= 400:
                        a["status"] = "HTTP_ERROR"
                    else:
                        status, title, size, links = inspect(response)
                        a.update(status=status, title=title, text_chars=size,
                                 links=links[:50], content_type=response.headers.get("Content-Type", ""))
                attempts.append(a)
                if a["status"] in ("READABLE", "JS_OR_THIN_PAGE", "BLOCKED_PAGE", "ACCESS_RESTRICTED"):
                    break  # Don't hammer a site that is blocking automated access.
                if a["status"] == "HTTP_ERROR" and a.get("http_status", 0) < 500:
                    break
            except requests.RequestException as exc:
                a.update(status="NETWORK_ERROR", error=str(exc)[:220])
                attempts.append(a)
            if number < self.retries:
                time.sleep(number)
        return attempts

    def render_js(self, url):
        """Render a genuinely JS-thin public page, never bypass 401/403/CAPTCHAs."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=18000)
                page.wait_for_timeout(3500)
                html = page.content()
                title = page.title()
                text = page.locator("body").inner_text(timeout=7000)
                browser.close()
            self.browser_jobs += 1
            if response is None or response.status >= 400:
                return {"status": "BROWSER_HTTP_ERROR", "http_status": response.status if response else None}
            if any(x in text.lower() for x in BAD_CONTENT):
                return {"status": "BLOCKED_PAGE", "title": title}
            substantive = len(text.strip()) >= 140 and (not any(k in urlparse(url).path.lower() for k in CALENDAR_PATHS) or DATE_ITEM.search(text))
            return {"status": "READABLE_JS" if substantive else "JS_OR_THIN_PAGE",
                    "title": title[:140], "text_chars": len(text), "html_chars": len(html)}
        except Exception as exc:
            return {"status": "BROWSER_ERROR", "error": str(exc)[:180]}

    def check(self, url):
        if url not in self.cache:
            attempts = self.attempt(url)
            latest = attempts[-1]
            result = {"url": url, "attempts": attempts,
                      "status": latest["status"], "final_url": latest.get("final_url", url)}
            if latest["status"] == "JS_OR_THIN_PAGE" and self.browser:
                br = self.render_js(result["final_url"])
                result["browser"] = br
                if br["status"] == "READABLE_JS":
                    result["status"] = "READABLE_JS"
            self.cache[url] = result
        return self.cache[url]


def fallback_candidates(urls, category, cache, other_company_urls=(), limit=10):
    """Discover FIRST-PARTY source pages already linked by the configured pages."""
    found = []
    allowed_hosts = {host(u) for u in [*urls, *other_company_urls]}
    for url in dict.fromkeys([*urls, *other_company_urls]):
        result = cache.get(url, {})
        for attempt in result.get("attempts", [])[-1:]:
            for link, label in attempt.get("links", []):
                if host(link) in allowed_hosts and relevant(link, label, category):
                    if link not in urls and link not in found:
                        found.append(link)
                if len(found) >= limit:
                    return found
    return found


def run(args):
    config = json.loads((ROOT / "source_overrides.json").read_text())
    sources = config["sources"]
    keys = list(sources)
    companies = {key.split("|", 1)[0] for key in keys}
    assert len(keys) == 75 and len(companies) == 25, "Expected 25 companies and 75 configured categories"
    assert all(key.rsplit("|", 1)[-1] in CATEGORIES for key in keys)
    unique = list(dict.fromkeys(url for urls in sources.values() for url in urls))
    audit = Auditor(retries=args.retries, browser=args.browser, timeout=args.timeout)
    report = {"generated_at": stamp(), "expected_categories": 75, "configured_urls": len(unique),
              "urls": {}, "categories": {}, "warning": "Network access only; NO calendar or dated-event verification."}
    path = ROOT / "source-probes" / (dt.datetime.now(dt.timezone.utc).date().isoformat() + "-access.json")
    path.parent.mkdir(parents=True, exist_ok=True)

    for i, url in enumerate(unique, 1):
        r = audit.check(url)
        report["urls"][url] = r
        print(f"[{i}/{len(unique)}] {r['status']:20s} {url}", flush=True)
        if i % 10 == 0:
            path.write_text(json.dumps(report, indent=2) + "\n")

    for key, urls in sources.items():
        readable = [u for u in urls if audit.cache[u]["status"] in ("READABLE", "READABLE_JS")]
        extra = []
        if not readable:
            # Official issuer links found on OTHER accessible pages for this same company.
            company = key.split("|", 1)[0]
            other_company_urls = [u for k, group in sources.items() if k.startswith(company + "|") for u in group]
            candidates = fallback_candidates(urls, key.rsplit("|", 1)[-1], audit.cache, other_company_urls)
            # SEC official EDGAR browse is a documented fallback for blocked data.sec.gov JSON.
            for u in urls:
                m = re.search(r"data\.sec\.gov/submissions/CIK(\d{10})\.json", u)
                if m:
                    candidates.append("https://www.sec.gov/edgar/browse/?CIK=" + str(int(m.group(1))) + "&owner=exclude")
            for candidate in dict.fromkeys(candidates):
                r = audit.check(candidate)
                extra.append({"url": candidate, "status": r["status"]})
                if r["status"] in ("READABLE", "READABLE_JS"):
                    readable.append(candidate)
                    break
        report["categories"][key] = {
            "status": ("SOURCE_REACHABLE" if any(u in urls for u in readable) else
                       "FALLBACK_REACHABLE" if readable else "UNRESOLVED"),
            "configured": urls, "readable_urls": readable,
            "fallback_attempts": extra,
            "editorial_verification": "NOT_PERFORMED",
        }

    report["completed_at"] = stamp()
    report["configured_url_statuses"] = dict(Counter(x["status"] for x in
                                               (audit.cache[u] for u in unique)))
    report["category_statuses"] = dict(Counter(x["status"] for x in report["categories"].values()))
    report["browser_attempts"] = audit.browser_jobs
    report["failing_urls"] = [u for u in unique if audit.cache[u]["status"] not in ("READABLE", "READABLE_JS")]
    report["unresolved_categories"] = [k for k, v in report["categories"].items() if v["status"] == "UNRESOLVED"]
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in (
        "expected_categories", "configured_urls", "configured_url_statuses",
        "category_statuses", "browser_attempts", "unresolved_categories")}, indent=2))
    print("Detailed report:", path.relative_to(ROOT))
    # Inaccessible URLs never masquerade as successful source checks.
    return 2 if report["unresolved_categories"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="Render JS-thin pages with Playwright Chromium")
    parser.add_argument("--timeout", type=int, default=16)
    parser.add_argument("--retries", type=int, default=2)
    raise SystemExit(run(parser.parse_args()))
