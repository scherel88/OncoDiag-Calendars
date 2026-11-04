#!/usr/bin/env python3
"""Ordinary Chromium rendering for public investor-source URLs blocked by HTTP clients.

This does not solve CAPTCHAs, authentication gates, origin IP bans, or reporting
verification. Never use it to assert seven-day source review or 'no changes'.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, Error as BrowserError

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
DATE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d\d\b|\b20\d\d[-/]\d\d[-/]\d\d\b", re.I)
KEYWORDS = re.compile(r"earnings|investor|quarter|conference|fireside|results|presentation|webcast|financial|sec filing", re.I)
BLOCK = re.compile(r"access denied|request blocked|captcha required|verify you are human|just a moment", re.I)


def render(context, url):
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=12000)
        status = response.status if response else None
        page.wait_for_timeout(650)
        body = page.locator("body").inner_text(timeout=5000)[:150000]
        if status != 200:
            return {"url": url, "status": status, "outcome": "blocked",
                    "reason": "Page returned non-200 status"}
        if len(body) < 100 or BLOCK.search(body[:2000]):
            return {"url": url, "status": status, "outcome": "unusable",
                    "reason": "Empty/challenge/access-denied page", "text_chars": len(body)}
        anchors = page.locator("a").evaluate_all(
            "xs => xs.slice(0, 400).map(x => ({title: (x.innerText||'').trim().slice(0,170),url:x.href})).filter(x=>x.title && x.url.startsWith('https://'))"
        )
        relevant = [x for x in anchors if KEYWORDS.search(x["title"])][:30]
        dates = list(dict.fromkeys(DATE.findall(body)))[:30]
        return {"url": url, "final_url": page.url, "status": status,
                "outcome": "rendered", "text_chars": len(body),
                "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "headings": page.locator("h1,h2,h3").all_inner_texts()[:15],
                "date_mentions": dates, "relevant_links": relevant,
                "content_review_verified": False}
    except (BrowserError, TimeoutError) as exc:
        return {"url": url, "outcome": "error", "reason": str(exc)[:300]}
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Eastern date YYYY-MM-DD")
    parser.add_argument("--max-checks", type=int, default=30)
    args = parser.parse_args()
    day = args.date or datetime.now(ET).date().isoformat()
    src = ROOT / "source-probes" / (day + ".json")
    data = json.loads(src.read_text(encoding="utf-8"))
    targets = [c for c in data["checks"] if c["http_retrieval"] == "failed"]
    targets = targets[:args.max_checks]
    report = {"date": day, "generated_at": datetime.now(ET).isoformat(),
              "source_report": str(src.relative_to(ROOT)),
              "scope": "Normal public headless browser rendered only for HTTP failures.",
              "not_a_completed_source_audit": True, "checks": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US")
        for c in targets:
            results = []
            best = None
            for url in c["configured_urls"][:4]:
                # Avoid needless browser navigation to JSON API endpoints, which
                # are better tested through the structured HTTP collector.
                if "/submissions/CIK" in url and url.endswith(".json"):
                    continue
                candidate = render(context, url)
                results.append(candidate)
                if candidate["outcome"] == "rendered":
                    best = candidate
                    break
            report["checks"].append({
                "company": c["company"], "category": c["category"],
                "browser_rendered": bool(best), "selected": best,
                "attempts": results, "source_category_verified": False,
                "needs_human_or_agent_announcement_review": True
            })
        browser.close()
    report["attempted_categories"] = len(report["checks"])
    report["browser_rendered_categories"] = sum(x["browser_rendered"] for x in report["checks"])
    report["still_unretrievable"] = (report["attempted_categories"] -
                                     report["browser_rendered_categories"])
    out = ROOT / "source-probes" / (day + ".browser.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, indent=2))
    for item in report["checks"]:
        if not item["browser_rendered"]:
            print("BROWSER FAILED", item["company"], item["category"])
    # Actual accessible/unavailable findings are data. Nonzero exit only on
    # workflow/software errors; do not suppress 403s or pretend success.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
