#!/usr/bin/env python3
"""Mandatory 26x3 source-attempt queue. Network access != verified editorial review."""
import datetime as dt
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

COMPANIES = ["Labcorp","Mendelics","Exact Sciences","Veracyte","Caris Life Sciences","Guardant Health","Myriad Genetics","Natera","Quest Diagnostics","NeoGenomics","DELFI Diagnostics","Personalis","GRAIL","Tempus AI","Adaptive Biotechnologies","BillionToOne","GeneDx","SOPHiA GENETICS","Illumina","PacBio","Oxford Nanopore Technologies","Element Biosciences","MGI Tech","Thermo Fisher Scientific","10x Genomics","Twist Bioscience"]
CATEGORIES = ["official_ir_events_webcasts","corporate_ir_press_releases_investor_conferences","official_filings_financial_reports"]
ROOT = pathlib.Path(__file__).resolve().parent
TODAY = dt.datetime.now(dt.timezone.utc).date()
LOOKBACK = TODAY - dt.timedelta(days=7)
DEST = ROOT / "source-probes" / (TODAY.isoformat() + ".json")

class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "OncoDiagInvestorAudit/1.0 (public issuer sources; contact repository owner)",
        "Accept": "text/html,application/json,application/xml,text/plain;q=0.8"})
    with urllib.request.urlopen(req, timeout=18) as response:
        body = response.read(700000).decode("utf-8", errors="replace")
        content_type = response.headers.get("Content-Type", "")
        if "html" in content_type:
            parser = VisibleText()
            parser.feed(body)
            body = " ".join(parser.parts)
        return response.status, response.geturl(), content_type, body

def main():
    overrides = json.loads((ROOT / "source_overrides.json").read_text())["sources"]
    ledger = []
    DEST.parent.mkdir(parents=True, exist_ok=True)
    for company_index, company in enumerate(COMPANIES, 1):
        for category_index, category in enumerate(CATEGORIES, 1):
            key = company + "|" + category
            urls = list(dict.fromkeys(overrides.get(key, [])))
            entry = {
                "company": company, "category": category,
                "company_index": company_index, "category_index": category_index,
                "status": "NOT_ATTEMPTED", "checked_at": None,
                "lookback_start": LOOKBACK.isoformat(), "lookback_end": TODAY.isoformat(),
                "attempted_urls": [], "evidence_urls": [], "errors": [],
                "dated_evidence": [], "retry_rounds": 0,
                "notes": "Network probe only; full archive coverage and event verification require substantive review."
            }
            if not urls:
                entry["notes"] = "EXECUTION GAP: No source URL configured. Add primary official URL to source_overrides.json."
            else:
                for round_number in range(1, 4):
                    entry["retry_rounds"] = round_number
                    for url in urls:
                        attempt = {"url": url, "timestamp": now(), "round": round_number}
                        entry["checked_at"] = attempt["timestamp"]
                        try:
                            status, final_url, content_type, body = fetch(url)
                            attempt.update({"http_status": status, "final_url": final_url,
                                            "content_type": content_type, "bytes_read": len(body)})
                            # A successful fetch is NOT evidence of a complete seven-day archive.
                            if len(body.strip()) >= 100:
                                entry["evidence_urls"].append(final_url)
                                entry["status"] = "SOURCE_ERROR"
                                entry["notes"] = "Source fetched, but seven-day dated archive and event changes NOT independently validated."
                                entry["attempted_urls"].append(attempt)
                                break
                            attempt["error"] = "Insufficient substantive page content"
                        except Exception as exc:
                            attempt["error"] = type(exc).__name__ + ": " + str(exc)[:240]
                        entry["attempted_urls"].append(attempt)
                        if "error" in attempt: entry["errors"].append(attempt["error"])
                    if entry["evidence_urls"]: break
                    if round_number < 3: time.sleep(round_number)
                if not entry["evidence_urls"]:
                    entry["status"] = "SOURCE_ERROR"
                    entry["notes"] = "All configured source attempts failed or yielded insufficient content."
            ledger.append(entry)
            # Persist after every category so interrupted jobs retain evidence in workspace.
            DEST.write_text(json.dumps({"date": TODAY.isoformat(), "generated_at": now(),
                "expected": 78, "checks": ledger}, indent=2) + "\n")
        assert len(ledger) == company_index * 3, "Company queue boundary failure"
    keys = {(x["company"], x["category"]) for x in ledger}
    assert len(ledger) == len(keys) == 78, "Coverage queue integrity failure"
    counts = {s: sum(x["status"] == s for x in ledger) for s in
              ["VERIFIED_NEW", "VERIFIED_NO_CHANGE", "NOT_APPLICABLE", "SOURCE_ERROR", "NOT_ATTEMPTED"]}
    payload = {"date": TODAY.isoformat(), "generated_at": now(), "expected": 78,
               "unique_keys": len(keys), "counts": counts, "checks": ledger,
               "warning": "All 78 queue entries processed, but HTTP fetches alone never establish 78 verified sources."}
    DEST.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"expected": 78, "unique_keys": len(keys), "counts": counts,
                      "output": str(DEST.relative_to(ROOT))}))
    # Do not mark an audit successful until all 78 substantive reviews have passed.
    if counts["SOURCE_ERROR"] or counts["NOT_ATTEMPTED"]: raise SystemExit(2)

if __name__ == "__main__": main()
