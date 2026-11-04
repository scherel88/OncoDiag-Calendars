#!/usr/bin/env python3
"""Fetch all 26 x 3 source-category URLs on GitHub's network.

This is an evidence/preflight collector, NOT a substitute for reviewing
announcements or verifying that a seven-day archive was fully checked.
Never convert a successful HTTP request into an audit 'verified' status.
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
UA = "DiagnosticsInvestorSourceMonitor/1.0 (+https://github.com/scherel88/diagnostics-earnings-calendar)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml,text/xml,*/*;q=0.7",
           "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.8"}
RELEVANT = re.compile(
    r"earnings|financial results|results conference|investor day|capital markets|investor conference|"
    r"investor event|presentation|fireside|webcast|participat|shareholder|q[1-4]\s*20\d\d|"
    r"fourth quarter|third quarter|second quarter|first quarter|annual results|half.year",
    re.I,
)
DATE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d\d\b"
    r"|\b20\d\d[-/]\d\d[-/]\d\d\b", re.I
)
SINGLE_ITEM = re.compile(r"/Archives/edgar/data/|news-release-details/|news-release-detail/|"
                         r"/20\d\d[-/]\d\d[-/]\d\d|/finalpage/", re.I)


class LinkReader(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self.text = base, [], []
        self.href = None
        self.anchor = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.suppressed += 1
        if tag == "a":
            d = dict(attrs)
            self.href = d.get("href")
            self.anchor = []

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            label = " ".join(" ".join(self.anchor).split())
            if label:
                url = urljoin(self.base, self.href)
                if url.startswith("https://"):
                    self.links.append({"title": label[:240], "url": url})
            self.href = None
            self.anchor = []
        if tag in ("script", "style", "noscript") and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data):
        if not self.suppressed:
            self.text.append(data)
            if self.href is not None:
                self.anchor.append(data)


def try_url(url):
    errors = []
    for attempt in range(1, 3):
        start = time.monotonic()
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=12) as resp:
                data = resp.read(2_000_001)
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    data = gzip.decompress(data)
                content_type = resp.headers.get("Content-Type", "").lower()
                final_url = resp.geturl()
                code = resp.status
            if code != 200:
                raise ValueError("HTTP " + str(code))
            if len(data) < 80:
                raise ValueError("Response has less than 80 bytes")
            is_html = "html" in content_type or (b"<html" in data[:500].lower())
            is_xml = "xml" in content_type or data[:50].lstrip().startswith(b"<?xml")
            is_json = "json" in content_type or data[:100].lstrip().startswith(b'{"cik"')
            parsed = {"candidate_links": [], "date_mentions": [],
                      "text_chars": 0, "links_seen": 0,
                      "filing_records": [], "page_signals": {}}
            if is_html:
                reader = LinkReader(final_url)
                reader.feed(data.decode("utf-8", "replace"))
                text = " ".join(" ".join(reader.text).split())
                parsed["text_chars"] = len(text)
                parsed["links_seen"] = len(reader.links)
                parsed["page_signals"] = {
                    "events": bool(re.search(r"upcoming events|past events|investor calendar|events (?:&|and) presentations", text, re.I)),
                    "news": bool(re.search(r"press releases|news releases|investor news|newsroom", text, re.I)),
                    "filings": bool(re.search(r"SEC filings|filing date|filing type|form 10-[KQ]|form 8-K|quarterly financial", text, re.I))
                }
                seen = set()
                for link in reader.links:
                    if RELEVANT.search(link["title"]) and link["url"] not in seen:
                        parsed["candidate_links"].append(link)
                        seen.add(link["url"])
                    if len(parsed["candidate_links"]) >= 35:
                        break
                parsed["date_mentions"] = list(dict.fromkeys(DATE.findall(text[:100000])))[:30]
            elif is_xml:
                text = data.decode("utf-8", "replace")
                parsed["text_chars"] = len(text)
                titles = re.findall(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
                parsed["xml_relevant_titles"] = [
                    re.sub(r"<[^>]+>", "", title).strip()[:220] for title in titles
                    if RELEVANT.search(title)
                ][:30]
                parsed["date_mentions"] = list(dict.fromkeys(DATE.findall(text[:100000])))[:30]
            elif is_json:
                document = json.loads(data.decode("utf-8", "replace"))
                if not isinstance(document, dict):
                    raise ValueError("JSON document is not an object")
                recent = document.get("filings", {}).get("recent", {})
                if not isinstance(recent, dict) or not isinstance(recent.get("form"), list):
                    raise ValueError("No structured company filings list in JSON")
                forms = recent.get("form", [])
                dates = recent.get("filingDate", [])
                accessions = recent.get("accessionNumber", [])
                docs = recent.get("primaryDocument", [])
                wanted = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A",
                          "6-K", "20-F", "40-F", "425", "SC 13E3", "SC 13E3/A"}
                for i, form in enumerate(forms):
                    if form in wanted and i < len(dates):
                        parsed["filing_records"].append({
                            "form": form, "filing_date": dates[i],
                            "accession": accessions[i] if i < len(accessions) else None,
                            "primary_document": docs[i] if i < len(docs) else None
                        })
                    if len(parsed["filing_records"]) >= 30:
                        break
                parsed["text_chars"] = len(data)
                parsed["page_signals"] = {"filings": True, "events": False, "news": False}
                parsed["issuer_cik"] = document.get("cik")
                parsed["issuer_name"] = document.get("name")
            return {"outcome": "fetched", "url": url, "final_url": final_url,
                    "http_status": code, "content_type": content_type[:120],
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    "elapsed_ms": int((time.monotonic() - start)*1000),
                    "is_html_or_xml": bool(is_html or is_xml or is_json), "attempts": attempt,
                    **parsed}, errors
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, EOFError) as exc:
            errors.append({"url": url, "attempt": attempt,
                           "error": str(exc)[:300]})
            if attempt == 1:
                time.sleep(1.0)
    return None, errors


def do_check(check, overrides):
    key = check["company"] + "|" + check["category"]
    urls = overrides.get(key, [])
    if not isinstance(urls, list):
        urls = []
    if check.get("source_url", "").startswith("https://"):
        urls = urls + [check["source_url"]]
    urls = list(dict.fromkeys(u for u in urls if isinstance(u, str) and u.startswith("https://")))[:4]
    begin = datetime.now(ET).isoformat()
    attempts, selected = [], None
    for url in urls:
        data, failures = try_url(url)
        attempts.extend(failures)
        if data:
            selected = data
            break
    result = {"company": check["company"], "category": check["category"],
              "started_at": begin, "finished_at": datetime.now(ET).isoformat(),
              "attempted": bool(urls), "configured_urls": urls,
              "attempt_errors": attempts,
              "http_retrieval": "success" if selected else "failed",
              "human_announcement_review_completed": False,
              "seven_day_newsroom_review_completed": False,
              "source_category_verified": False,
              "source_quality_warnings": [],
              "retrieved": selected}
    if not urls:
        result["source_quality_warnings"].append("No HTTPS source URL configured")
    elif selected:
        u = selected["url"]
        if not selected["is_html_or_xml"]:
            result["source_quality_warnings"].append("Binary response: content not machine-reviewed")
        if selected["text_chars"] < 250 and selected["is_html_or_xml"]:
            result["source_quality_warnings"].append(
                "Sparse page content; likely JavaScript rendering, blocking or empty feed")
        if SINGLE_ITEM.search(urlsplit(u).path):
            result["source_quality_warnings"].append(
                "One-off historical article or static filing is not a current archive")
        if not selected.get("candidate_links") and not selected.get("xml_relevant_titles"):
            result["source_quality_warnings"].append(
                "No investor-relevant anchor titles extracted; manual inspection needed")
        if check["category"] == "corporate_ir_press_releases_investor_conferences":
            result["source_quality_warnings"].append(
                "HTTP retrieval alone cannot establish a complete last-seven-day newsroom review")
    else:
        result["source_quality_warnings"].append(
            "All configured URLs failed after retries; inspect attempt_errors")
        if attempts and all("403" in e["error"] for e in attempts):
            result["source_quality_warnings"].append(
                "GitHub runner was forbidden (HTTP 403) on every URL; this does NOT prove public browser access is unavailable. Retry with browser-based official-source inspection.")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Eastern date YYYY-MM-DD; defaults to today")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    date = args.date or datetime.now(ET).date().isoformat()
    audits = sorted((ROOT / "audit").glob("????-??-??.json"))
    if not audits:
        raise SystemExit("No existing 26x3 audit records to seed source registry")
    seed = json.loads(audits[-1].read_text(encoding="utf-8"))
    checks = seed.get("checks", [])
    keys = {(c.get("company"), c.get("category")) for c in checks}
    if len(checks) != 78 or len(keys) != 78:
        raise SystemExit("Incomplete source registry: 78 distinct company/category pairs required")
    overrides = json.loads((ROOT / "source_overrides.json").read_text(encoding="utf-8"))["sources"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda c: do_check(c, overrides), checks))
    attempted = sum(c["attempted"] for c in results)
    fetched = sum(c["http_retrieval"] == "success" for c in results)
    sparse = sum(any("Sparse page" in w for w in c["source_quality_warnings"]) for c in results)
    historical = sum(any("One-off historical" in w for w in c["source_quality_warnings"]) for c in results)
    report = {
        "audit_date": date,
        "generated_at": datetime.now(ET).isoformat(),
        "seed_source": str(audits[-1].relative_to(ROOT)),
        "source_pairs": 78, "url_attempted": attempted, "http_fetched": fetched,
        "http_failed": 78 - fetched, "sparse_dynamic_or_empty": sparse,
        "historical_one_off_sources": historical,
        "completed_independent_announcement_reviews": 0,
        "disclaimer": "Network-level preflight and candidate-link extraction only. HTTP success does NOT verify news, events, filings, 7-day coverage, or absence of changes.",
        "checks": results
    }
    output = ROOT / "source-probes" / (date + ".json")
    output.parent.mkdir(exist_ok=True)
    if not args.dry_run:
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, indent=2))
    for item in results:
        if item["http_retrieval"] == "failed":
            print("FETCH FAILED", item["company"], item["category"],
                  "; ".join(x["error"] for x in item["attempt_errors"][-2:]))
    if attempted != 78:
        raise SystemExit("Preflight coverage failure: not all 78 source-category URLs attempted")


if __name__ == "__main__":
    main()
