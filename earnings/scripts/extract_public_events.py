#!/usr/bin/env python3
"""Extract public issuer investor events to a review queue; never publish ICS.

One shared Chromium, bounded concurrent browser contexts, event DOM/JSON-LD/XHR,
archive selectors, load-more and issuer-hosted detail pages. Errors and empty
JavaScript shells are explicit, never represented as 'no upcoming events'.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

EVENT_WORDS = ("earnings", "financial results", "conference", "investor", "fireside",
               "presentation", "analyst", "capital markets", "shareholder", "full year",
               "half year", "results call", "webcast", "healthcare", "growth stock")
BAD_WORDS = ("verify you are human", "captcha", "access denied",
             "checking your browser", "enable cookies", "just a moment")
DATE_RE = re.compile(
    r"\b(?:20\d{2}[-/]\d\d?[-/]\d\d?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember|t)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
    r"\s+\d{1,2},?\s+20\d{2}|"
    r"\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember|t)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+20\d{2})\b",re.I)
TIME_RE = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)\b", re.I)
ZONE_RE = re.compile(r"\b(Eastern Time|Central Time|Pacific Time|Mountain Time|EST|EDT|ET|CST|CDT|CT|PST|PDT|PT|MST|MDT|MT|UTC|GMT|BST|CET|CEST)\b", re.I)
H24_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\s*(?:a\.?m\.?|p\.?m\.?))", re.I)
FIXED = {"EST":-5,"EDT":-4,"CST":-6,"CDT":-5,"PST":-8,"PDT":-7,
         "MST":-7,"MDT":-6,"UTC":0,"GMT":0}
ZONES = {"ET":"America/New_York","CT":"America/Chicago","PT":"America/Los_Angeles",
         "MT":"America/Denver","BST":"Europe/London","CET":"Europe/Paris","CEST":"Europe/Paris",
         "EASTERN TIME":"America/New_York","CENTRAL TIME":"America/Chicago",
         "PACIFIC TIME":"America/Los_Angeles","MOUNTAIN TIME":"America/Denver"}
CARD_SELECTOR = ("article, tr, li, [itemscope][itemtype*='Event'], "
                 "[class*='event-item'], [class*='event_item'], [class*='eventItem'], "
                 "[class*='event-row'], [class*='eventRow'], .q4-event, .module_item")
STAMP_RE = re.compile(r"\b20\d\d-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)\b",re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def clean_url(base: str, raw: str | None) -> str | None:
    if not raw:
        return None
    u = urlsplit(urljoin(base, raw.strip()))
    if u.scheme not in ("https", "http") or not u.netloc:
        return None
    return urlunsplit((u.scheme, u.netloc, u.path, u.query, ""))


def same_issuer(url: str, issuer: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    reference = (urlsplit(issuer).hostname or "").lower()
    if not host or not reference:
        return False
    # Exact host or sibling subdomains under the same registrable issuer suffix.
    return host == reference or host.endswith("." + reference) or reference.endswith("." + host)


def relevant(title: str) -> bool:
    t = compact(title).lower()
    return 10 <= len(t) <= 210 and any(k in t for k in EVENT_WORDS)


def parse_start(raw: str) -> tuple[str | None, str | None, str | None]:
    """Never invent a clock or zone; timezone abbreviation ET is DST-aware."""
    raw = compact(raw)
    iso = STAMP_RE.search(raw)
    if iso:
        try:
            d = datetime.fromisoformat(iso.group().replace("Z", "+00:00"))
            return d.isoformat(), d.date().isoformat(), None
        except ValueError:
            pass
    m = DATE_RE.search(raw)
    if not m:
        return None, None, "date_missing"
    try:
        d = dateparser.parse(m.group(), fuzzy=True).date()
    except (ValueError, TypeError, OverflowError):
        return None, None, "date_invalid"
    # Limit the clock/zone to the SAME event card's date context.
    near = raw[max(0,m.start()-25):min(len(raw),m.end()+95)]
    t = TIME_RE.search(near)
    h24 = None if t else H24_RE.search(near)
    if not t and not h24:
        return None, d.isoformat(), "time_missing"
    anchor = t or h24
    after = near[anchor.end():anchor.end()+35]
    before = near[max(0,anchor.start()-20):anchor.start()]
    zone = ZONE_RE.search(after) or ZONE_RE.search(before)
    if not zone:
        return None, d.isoformat(), "timezone_missing"
    code = zone.group().upper()
    tz = timezone(timedelta(hours=FIXED[code])) if code in FIXED else ZoneInfo(ZONES[code])
    hr = int(t.group(1)) % 12 + (12 if t.group(3).lower().startswith("p") else 0) if t else int(h24.group(1))
    minute = int(t.group(2) or 0) if t else int(h24.group(2))
    dtime = datetime(d.year,d.month,d.day,hr,minute,tzinfo=tz)
    return dtime.isoformat(), d.isoformat(), None


@dataclass
class Candidate:
    company: str
    title: str
    event_date: str | None
    start: str | None
    event_url: str
    webcast_url: str | None
    listing_url: str
    extraction_method: str
    excerpt: str
    missing: str | None

    @property
    def identity(self):
        normalized = re.sub(r"[^a-z0-9]", "", self.title.lower())[:120]
        return hashlib.sha256(f"{self.company}|{normalized}|{self.event_date}".encode()).hexdigest()[:24]

    @property
    def review_ready(self):
        # A bare calendar landing page is NOT a valid substitute for event detail.
        return bool(self.start and (self.webcast_url or self.event_url != self.listing_url)
                    and self.missing is None)


def from_json(company: str, data, base: str, method: str) -> list[Candidate]:
    out = []
    stack = [data]
    count = 0
    while stack and count < 9000:
        x = stack.pop()
        count += 1
        if isinstance(x, list):
            stack.extend(x[:1200])
            continue
        if not isinstance(x, dict):
            continue
        stack.extend(v for v in x.values() if isinstance(v,(dict,list)))
        lc = {str(k).lower():v for k,v in x.items()}
        kind = str(lc.get("@type","")).lower()
        title = next((lc.get(k) for k in ("eventname","eventtitle","title","name")
                      if isinstance(lc.get(k),str)), None)
        if not title or not relevant(title):
            continue
        raw_date = next((lc.get(k) for k in ("startdatetime","startdate","eventstartdate","eventdate","date","start")
                         if isinstance(lc.get(k),str)), None)
        if not raw_date:
            continue
        # Do not transform a generic article date into an investor event.
        if method.endswith("json-ld") and "event" not in kind:
            continue
        raw = " ".join(str(i) for i in (raw_date,
                      lc.get("starttime",""), lc.get("timezone","")) if isinstance(i,str))
        start, day, missing = parse_start(raw)
        if not day:
            continue
        link = next((lc.get(k) for k in ("eventurl","detailurl","url","link","eventlink")
                     if isinstance(lc.get(k),str)),None)
        web = next((lc.get(k) for k in ("webcasturl","webcastlink","webcast")
                    if isinstance(lc.get(k),str)),None)
        event_url = clean_url(base,link) or base
        # Only record issuer event pages as event_url. Third-party URLs are webcasts.
        web_url = clean_url(base,web)
        if not same_issuer(event_url,base):
            web_url = web_url or event_url
            event_url = base
        out.append(Candidate(company,compact(title),day,start,event_url,web_url,
                             base,method,compact(raw)[:300],missing))
    return out


def from_html(company: str, html: str, base: str, method: str) -> list[Candidate]:
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    for script in soup.select('script[type="application/ld+json"], script#__NEXT_DATA__, script[type="application/json"]')[:28]:
        try:
            payload=json.loads(script.string or script.get_text())
            out.extend(from_json(company,payload,base,method+":json-ld" if
                                 script.get("type")=="application/ld+json" else method+":embedded"))
        except (TypeError,ValueError):
            continue
    for node in soup.select(CARD_SELECTOR)[:1600]:
        content=compact(node.get_text(" ",strip=True))
        if not 20<=len(content)<=1000 or len(DATE_RE.findall(content))!=1:
            continue
        titles=[compact(n.get_text(" ",strip=True)) for n in
                node.select("h2,h3,h4,h5,[class*='event-title'],[class*='eventTitle'],a[href]")[:20]]
        title=next((t for t in titles if relevant(t)),None)
        if not title:
            continue
        stamp=content
        for t in node.select("time[datetime]"):
            raw=t.get("datetime","")
            if STAMP_RE.search(raw):
                stamp=raw
                break
        start,day,missing=parse_start(stamp)
        if not day:
            continue
        event_url=base
        webcast=None
        for anchor in node.select("a[href]")[:30]:
            url=clean_url(base,anchor.get("href"))
            if not url:
                continue
            label=compact(anchor.get_text(" ",strip=True)).lower()
            if any(w in label for w in ("webcast","watch","listen","replay","stream")):
                webcast=url
            if relevant(label) and same_issuer(url,base):
                event_url=url
        out.append(Candidate(company,title,day,start,event_url,webcast,base,
                             method+":dom",content[:400],missing))
    return out


def merge(rows: list[Candidate]) -> list[Candidate]:
    bykey={}
    for item in rows:
        if not item.event_date:
            continue
        key=item.identity
        if key not in bykey:
            bykey[key]=item
        else:
            old=bykey[key]
            if not old.start and item.start:
                old.start,old.missing=item.start,item.missing
            if not old.webcast_url and item.webcast_url:
                old.webcast_url=item.webcast_url
            if old.event_url==old.listing_url and item.event_url!=item.listing_url:
                old.event_url=item.event_url
            if item.extraction_method not in old.extraction_method:
                old.extraction_method+=" + "+item.extraction_method
    return sorted(bykey.values(),key=lambda x:(x.event_date or "",x.company,x.title))


async def scan_company(browser, sem, company: dict, years: list[int], timeout_ms: int, max_details: int):
    from playwright.async_api import Error as PWError
    name=company["company"]; url=company["event_urls"][0]
    log={"company":name,"primary_url":url,"mode":company["mode"],
         "checked_at_utc":now(),"status":"NOT_ATTEMPTED","errors":[],
         "views":[],"network_event_payloads":0,"detail_pages":0}
    found=[]
    async with sem:
        ctx=await browser.new_context(locale="en-US",viewport={"width":1280,"height":850},
                                      service_workers="block")
        page=await ctx.new_page()
        responses=[]
        pending=[]
        def on_response(resp):
            if (resp.status==200 and ("json" in resp.headers.get("content-type","").lower())
                and ("event" in resp.url.lower() or "calendar" in resp.url.lower())
                and (same_issuer(resp.url,url) or any(
                    (urlsplit(resp.url).hostname or "").endswith("."+vendor)
                    for vendor in ("q4inc.com","gcs-web.com","q4web.com")))):
                async def extract_response():
                    try:
                        body=await resp.body()
                        if len(body)>850_000:
                            return
                        extracted=from_json(name,json.loads(body),url,"browser:observed-xhr")
                        found.extend(extracted)
                        if extracted:
                            log["network_event_payloads"]+=1
                    except (ValueError,PWError,RuntimeError):
                        pass
                pending.append(asyncio.create_task(extract_response()))
        page.on("response",on_response)
        async def capture(label):
            try:
                html=await page.content()
                rows=from_html(name,html,page.url or url,f"browser:{label}")
                found.extend(rows)
                log["views"].append({"view":label,"url":page.url,"events_detected":len(rows)})
            except (PWError,ValueError) as ex:
                log["errors"].append("capture: "+str(ex)[:170])
        try:
            response=await page.goto(url,wait_until="domcontentloaded",timeout=timeout_ms)
            status=response.status if response else None
            log["http_status"]=status
            log["final_url"]=page.url
            if status in (401,403,429):
                log["status"]="ACCESS_RESTRICTED"
            elif status is None or status>=400:
                log["status"]="HTTP_ERROR"
            else:
                await page.wait_for_timeout(950)
                body=(await page.locator("body").inner_text(timeout=4000)).lower()[:1400]
                if any(s in body for s in BAD_WORDS):
                    log["status"]="CHALLENGE_PAGE"
                else:
                    await capture("initial")
                    for _ in range(2):
                        await page.mouse.wheel(0,1100)
                        await page.wait_for_timeout(280)
                    await capture("after_scroll")
                    # Select each available archive year in a REAL visible year select.
                    selects=page.locator("select")
                    for i in range(min(await selects.count(),8)):
                        select=selects.nth(i)
                        try:
                            opts=await select.locator("option").evaluate_all(
                                "(els) => els.map(e=>({label:e.textContent.trim(),value:e.value}))")
                            year_opts=[o for o in opts if any(str(y) in
                                       (o["label"]+" "+o["value"]) for y in years)]
                            if len(year_opts)<2:
                                continue
                            for option in year_opts[:len(years)]:
                                await select.select_option(value=option["value"],timeout=2500)
                                await page.wait_for_timeout(500)
                                await capture("year:"+str(option["label"])[:15])
                        except (PWError,ValueError) as ex:
                            log["errors"].append("archive: "+str(ex)[:110])
                    # Read public past/upcoming event tabs as separate views.
                    for label in ("Past Events", "Upcoming Events"):
                        for role in ("tab", "button"):
                            try:
                                tab=page.get_by_role(role,name=label,exact=True)
                                if await tab.count() and await tab.first.is_visible():
                                    await tab.first.click(timeout=2000)
                                    await page.wait_for_timeout(450)
                                    await capture("tab:"+label)
                                    break
                            except PWError:
                                continue
                    # Bound the visible 'load more' UI interactions.
                    for j in range(3):
                        btn=page.get_by_text(re.compile(r"^(load more|view more|show more)$",re.I))
                        try:
                            if not await btn.count() or not await btn.first.is_visible():
                                break
                            await btn.first.click(timeout=2400)
                            await page.wait_for_timeout(480)
                            await capture("load_more:"+str(j+1))
                        except PWError:
                            break
                    # Details are sampled only when they can fill missing time/link.
                    details=list(dict.fromkeys(e.event_url for e in found
                              if e.event_url!=e.listing_url and same_issuer(e.event_url,url)
                              and (not e.start or not e.webcast_url)))
                    for link in details[:max_details]:
                        dp=await ctx.new_page()
                        try:
                            res=await dp.goto(link,wait_until="domcontentloaded",timeout=min(timeout_ms,12000))
                            if res and res.status==200:
                                await dp.wait_for_timeout(300)
                                found.extend(from_html(name,await dp.content(),link,"browser:issuer-detail"))
                                log["detail_pages"]+=1
                        except PWError as ex:
                            log["errors"].append("detail: "+str(ex)[:100])
                        finally:
                            await dp.close()
                    log["status"]="RETRIEVED"
            if pending:
                await asyncio.gather(*pending,return_exceptions=True)
        except PWError as ex:
            log["status"]="BROWSER_ERROR";log["errors"].append(str(ex)[:230])
        finally:
            await ctx.close()
    clean=merge(found)
    log["candidate_count"]=len(clean)
    log["review_ready_count"]=sum(x.review_ready for x in clean)
    log["status"]="CANDIDATES_FOUND" if clean else (
        "NO_EVENT_CANDIDATES_NOT_VERIFIED" if log["status"]=="RETRIEVED" else log["status"])
    if company["mode"]=="unconfirmed":
        log["warning"]="MGI: No issuer-owned dated investor events list independently confirmed."
    return clean,log


def load(path: Path) -> list[dict]:
    root=json.loads(path.read_text(encoding="utf-8"))
    if root.get("schema_version")!=1:
        raise ValueError("Unsupported source registry")
    companies=root["companies"]
    if len(companies)!=25 or sum(x["kind"]=="public" for x in companies)!=23:
        raise ValueError("Expected 23 public and two private companies")
    for c in companies:
        if not c["news_urls"] or (c["kind"]=="public" and not c["event_urls"]) or (
             c["kind"]=="private" and c["event_urls"]):
            raise ValueError("Invalid public/private source scope for "+c["company"])
    return companies


async def run(args):
    from playwright.async_api import async_playwright
    companies=[c for c in load(args.config) if c["kind"]=="public"]
    if args.company:
        companies=[c for c in companies if c["company"].lower() in
                   {n.lower() for n in args.company}]
        if not companies:
            raise ValueError("No matching company")
    years=[int(x) for x in args.years.split(",")]
    report={"schema_version":1,"generated_at_utc":now(),"extractor":"Playwright public browser",
            "review_required":True,"events":[],"companies":{},"errors":[],
            "notes":["Browser extraction is discovery evidence only; ChatGPT confirms all events.",
                     "A zero-result site does NOT prove no new events.",
                     "Private companies, SEC filings and conference discovery are not scanned."]}
    sem=asyncio.Semaphore(args.concurrency)
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        try:
            # A queued company must not time out before a browser slot opens.
            async def protected(company):
                async with sem:
                    attempts=[]
                    all_rows=[]
                    for candidate_url in company["event_urls"][:3]:
                        configured=dict(company,event_urls=[candidate_url])
                        try:
                            rows,stat=await asyncio.wait_for(
                                scan_company(browser,asyncio.Semaphore(1),configured,years,
                                             args.timeout_ms,args.max_details),
                                timeout=args.site_deadline)
                            attempts.append(stat)
                            all_rows.extend(rows)
                            if rows:
                                break  # avoid redundant work after a useful first-page extract
                        except Exception as ex:
                            attempts.append({"primary_url":candidate_url,
                                "status":"TIMED_OUT_OR_FAILED","errors":[str(ex)[:180]]})
                    final=attempts[-1]
                    final["configured_primary_url"]=company["event_urls"][0]
                    final["attempted_urls"]=[a["primary_url"] for a in attempts]
                    final["attempts"]=attempts
                    clean=merge(all_rows)
                    final["candidate_count"]=len(clean)
                    final["review_ready_count"]=sum(x.review_ready for x in clean)
                    if clean:
                        final["status"]="CANDIDATES_FOUND"
                    return clean,final
            tasks=[protected(c) for c in companies]
            outcomes=await asyncio.gather(*tasks,return_exceptions=True)
            for c,result in zip(companies,outcomes):
                name=c["company"]
                if isinstance(result,BaseException):
                    report["companies"][name]={"company":name,"primary_url":c["event_urls"][0],
                        "checked_at_utc":now(),"status":"TIMED_OUT_OR_FAILED","errors":[str(result)[:240]],
                        "candidate_count":0}
                else:
                    items,stat=result
                    report["companies"][name]=stat
                    report["events"].extend(asdict(x)|{"id":x.identity,
                        "ready_for_manual_review":x.review_ready} for x in items)
        finally:
            await browser.close()
    report["completed_at_utc"]=now()
    report["summary"]={"public_companies_checked":len(report["companies"]),
                       "event_candidates":len(report["events"]),
                       "sites_without_candidates":sum(v.get("candidate_count",0)==0
                         for v in report["companies"].values()),
                       "sites_with_access_errors":sum(v["status"] in ("ACCESS_RESTRICTED","CHALLENGE_PAGE",
                         "BROWSER_ERROR","HTTP_ERROR","TIMED_OUT_OR_FAILED") for v in report["companies"].values())}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temp=args.output.with_suffix(args.output.suffix+".tmp")
    temp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(report["summary"],indent=2))
    return 2 if report["summary"]["sites_with_access_errors"] == len(companies) else 0


def self_test():
    html = """<article><h3>Q3 Earnings Conference Call</h3>
    <time datetime="2026-11-05T16:30:00-05:00"></time>
    <a href="/events/detail">Q3 Earnings Conference Call</a>
    <a href="https://webcast.example/watch">Listen to webcast</a></article>"""
    rows=from_html("TestCo",html,"https://issuer.example/events","test")
    assert len(rows)==1 and rows[0].start=="2026-11-05T16:30:00-05:00"
    assert rows[0].review_ready
    js='{"@context":"https://schema.org","@type":"Event","name":"Q4 Earnings Conference Call","startDate":"2026-11-07T08:00:00-05:00","url":"https://issuer.example/events/q4"}'
    rows=from_json("TestCo",json.loads(js),"https://issuer.example/events","test:json-ld")
    assert len(rows)==1 and rows[0].start and rows[0].review_ready
    assert parse_start("Nov 7, 2026 at 8:30 AM ET")[0]=="2026-11-07T08:30:00-05:00"
    assert parse_start("Nov 7, 2026 at 8:30 AM")[2]=="timezone_missing"
    assert parse_start("17 November 2026 14:45 GMT")[0]=="2026-11-17T14:45:00+00:00"
    print("Self-test: 5 parser checks passed")


def cli():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=Path("earnings/config/sources.json"))
    parser.add_argument("--output",type=Path,default=Path("earnings/extractions/latest.json"))
    parser.add_argument("--company",action="append")
    parser.add_argument("--years",default=",".join(map(str,[date.today().year-1,date.today().year,
                                                        date.today().year+1])))
    parser.add_argument("--concurrency",type=int,default=3)
    parser.add_argument("--timeout-ms",type=int,default=22000)
    parser.add_argument("--max-details",type=int,default=10)
    parser.add_argument("--site-deadline",type=int,default=130)
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test();return 0
    if not 1<=args.concurrency<=5:
        parser.error("Concurrency must be between 1 and 5")
    return asyncio.run(run(args))


if __name__=="__main__":
    raise SystemExit(cli())
