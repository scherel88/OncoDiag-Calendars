#!/usr/bin/env python3
"""Extract public issuer investor events to a review queue; never publish ICS.

HTTP first, with conditional caching, linked calendars/feeds and issuer detail
pages; Chromium renders only pages that need JavaScript. All output remains
unverified discovery evidence, including timestamps with conflicting sources.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

EVENT_WORDS = ("earnings", "financial results", "conference", "investor", "fireside",
               "presentation", "analyst", "capital markets", "full year",
               "half year", "half-year", "results call", "webcast", "healthcare", "growth stock",
               "financial earnings", "trading update", "financial dates", "healthconx", "mrd day")
BAD_WORDS = ("verify you are human", "captcha", "access denied",
             "checking your browser", "enable cookies", "just a moment")
DATE_RE = re.compile(
    r"\b(?:20\d{2}[-/]\d\d?[-/]\d\d?|"
    r"\d{1,2}/\d{1,2}/20\d{2}|"
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
         "MST":-7,"MDT":-6,"UTC":0,"GMT":0,"BST":1,"CET":1,"CEST":2}
ZONES = {"ET":"America/New_York","CT":"America/Chicago","PT":"America/Los_Angeles",
         "MT":"America/Denver","BST":"Europe/London","CET":"Europe/Paris","CEST":"Europe/Paris",
         "EASTERN TIME":"America/New_York","CENTRAL TIME":"America/Chicago",
         "PACIFIC TIME":"America/Los_Angeles","MOUNTAIN TIME":"America/Denver"}
CARD_SELECTOR = ("article, tr, li, [itemscope][itemtype*='Event'], "
                 "[class*='event-item'], [class*='event_item'], [class*='eventItem'], "
                 "[class*='event-row'], [class*='eventRow'], [class*='event-card'], "
                 "[class*='event_item'], [class*='nir-event'], .views-row, .uk-card, "
                 ".wd_event, .wd_events_event, .q4-event, .module_item, .top-cell")
STAMP_RE = re.compile(r"\b20\d\d-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)\b",re.I)
SEASONAL_ZONES = {"EST":"America/New_York","EDT":"America/New_York",
                  "CST":"America/Chicago","CDT":"America/Chicago",
                  "PST":"America/Los_Angeles","PDT":"America/Los_Angeles",
                  "BST":"Europe/London","CET":"Europe/Paris","CEST":"Europe/Paris"}
MAX_BODY = 2_000_000
USER_AGENT = "Mozilla/5.0 (compatible; OncoDiag-Calendars/2.0; +https://github.com/scherel88/OncoDiag-Calendars)"
SHARED_HOSTS = {"gcs-web.com","q4web.com","q4inc.com","q4cdn.com","q4app.com"}
DETAIL_RE = re.compile(r"event-details|/events?/[^/?]+|[?&](?:item|eventid|event_id)=",re.I)


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
    if host == reference or host.endswith("." + reference) or reference.endswith("." + host):
        return True
    # Sibling corporate subdomains are valid; two tenants of an IR host are not.
    def suffix(h):
        parts=h.split(".")
        size=3 if ".".join(parts[-2:]) in ("co.uk","com.cn","com.au","co.jp") else 2
        return ".".join(parts[-size:])
    root=suffix(reference)
    return root not in SHARED_HOSTS and suffix(host)==root


def relevant(title: str) -> bool:
    t = compact(title).lower()
    return (10 <= len(t) <= 210 and any(k in t for k in EVENT_WORDS) and
            not re.match(r"^(?:presentation|webcast|replay|watch|listen)(?:\s*\(opens|\s+(?:pdf|mp3|mp4)\b|\s*$)",t) and
            not any(x in t for x in ("proxy card","annual general meeting",
                "annual meeting of stockholders","annual shareholder meeting")))


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
    near = raw[max(0,m.start()-25):min(len(raw),m.end()+160)]
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
    issue=None
    if isinstance(tz,ZoneInfo) and dtime.replace(fold=0).utcoffset()!=dtime.replace(fold=1).utcoffset():
        issue="ambiguous_or_nonexistent_local_time"
    if code in SEASONAL_ZONES and dtime.utcoffset()!=dtime.replace(
            tzinfo=ZoneInfo(SEASONAL_ZONES[code])).utcoffset():
        issue="timezone_abbreviation_conflicts_with_season"
    # Multiple displayed clocks must describe the same instant. Myriad has
    # published contradictory PT/ET clocks; leave those records for review.
    clocks=list(TIME_RE.finditer(near))
    for other in clocks[1:]:
        if re.fullmatch(r"\s*[-–—]\s*",near[anchor.end():other.start()]):
            continue  # An event's end time is not a contradictory second timezone.
        zm=ZONE_RE.search(near[other.end():other.end()+18])
        if not zm:
            continue
        z=zm.group().upper()
        other_tz=timezone(timedelta(hours=FIXED[z])) if z in FIXED else ZoneInfo(ZONES[z])
        hour=int(other.group(1))%12+(12 if other.group(3).lower().startswith("p") else 0)
        other_dt=datetime(d.year,d.month,d.day,hour,int(other.group(2) or 0),tzinfo=other_tz)
        if other_dt.astimezone(timezone.utc)!=dtime.astimezone(timezone.utc):
            issue="conflicting_displayed_times"
    return dtime.isoformat(), d.isoformat(), issue


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
    evidence: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def identity(self):
        normalized = re.sub(r"[^a-z0-9]", "", self.title.lower())[:120]
        return hashlib.sha256(f"{self.company}|{normalized}|{self.event_date}".encode()).hexdigest()[:24]

    @property
    def review_ready(self):
        # A bare calendar landing page is NOT a valid substitute for event detail.
        return bool(self.start and (self.webcast_url or self.event_url != self.listing_url)
                    and self.missing is None and not self.conflicts)


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
        raw_date = next((lc.get(k) for k in ("startdatetz","eventdatetz","startdatetime","startdate","eventstartdate","eventdate","start")
                         if isinstance(lc.get(k),str)), None)
        if not raw_date:
            continue
        # Do not transform a generic article date into an investor event.
        if method.endswith("json-ld") and "event" not in kind:
            continue
        raw = " ".join(str(i) for i in (raw_date,
                      lc.get("starttime",lc.get("eventtime","")),
                      lc.get("timezone",lc.get("eventtimezone",""))) if isinstance(i,str))
        start, day, missing = parse_start(raw)
        if not day:
            continue
        link = next((lc.get(k) for k in ("eventurl","detailurl","url","link","eventlink","linktoevent")
                     if isinstance(lc.get(k),str)),None)
        web = next((lc.get(k) for k in ("webcasturl","webcastlink","webcast")
                    if isinstance(lc.get(k),str)),None)
        event_url = clean_url(base,link) or base
        if re.search(r"\.(?:pdf|pptx?|docx?|xlsx?)(?:$|\?)",event_url,re.I):
            event_url=base
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
    for script in soup.select("script,style,nav,header,footer"):
        script.decompose()
    # Some financial calendars are a paragraph of bold dates separated by BRs.
    # These date-only candidates deliberately remain pending for a call time.
    for p in soup.select("main p, [role='main'] p, .prose p")[:300]:
        for strong in p.find_all("strong",recursive=False):
            stamp=compact(strong.get_text(" ",strip=True))
            if not DATE_RE.fullmatch(stamp):
                continue
            chunks=[]
            for sibling in strong.next_siblings:
                if getattr(sibling,"name",None)=="strong":
                    break
                chunks.append(sibling.get_text(" ",strip=True) if hasattr(sibling,"get_text") else str(sibling))
            title=compact(" ".join(chunks))
            if relevant(title):
                start,day,missing=parse_start(stamp)
                if day:
                    out.append(Candidate(company,title,day,start,base,None,base,
                        method+":financial-dates",stamp+" "+title,missing))
    nodes=list(soup.select(CARD_SELECTOR)[:1600])
    # Template classes change frequently. Walk outward from event headings and
    # links until date, title and webcast share one small, single-event block.
    seen={id(n) for n in nodes}
    for anchor in soup.select("a[href],h1,h2,h3,h4,h5")[:2500]:
        if not relevant(anchor.get_text(" ",strip=True)):
            continue
        node=anchor
        for _ in range(7):
            if not node or node.name in ("body","html"):
                break
            text=compact(node.get_text(" ",strip=True))
            if len(text)>1800:
                break
            if DATE_RE.search(text) or node.select_one("time[datetime],[itemprop='startDate']"):
                if id(node) not in seen:
                    nodes.append(node);seen.add(id(node))
            node=node.parent
    for node in nodes:
        content=compact(node.get_text(" ",strip=True))
        datetime_attrs=[t.get("datetime",t.get("content","")) for t in
                        node.select("time[datetime],[itemprop='startDate'][content]")]
        iso_attr=next((v for v in datetime_attrs if STAMP_RE.search(v)),None)
        try:
            dates={dateparser.parse(m.group(),fuzzy=True).date() for m in DATE_RE.finditer(content)}
        except (ValueError,OverflowError):
            continue
        # JS calendars often keep the only date in <time datetime>, not visible text.
        if not 20<=len(content)<=1800 or len(dates)>1 or (not dates and not datetime_attrs):
            continue
        titles=[compact(n.get_text(" ",strip=True)) for n in
                node.select("h1,h2,h3,h4,h5,.wd_title,.title,[class*='nir-asset-title'],"
                            "[class*='nir-event-title'],[class*='event-title'],[class*='eventTitle'],a[href]")[:30]]
        if not titles and node.name in ("h1","h2","h3","h4","h5"):
            titles=[compact(node.get_text(" ",strip=True))]
        title=next((t for t in titles if relevant(t)),None)
        if not title:
            continue
        stamp=iso_attr or (" ".join(datetime_attrs)+" "+content)
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
            if ((relevant(label) or DETAIL_RE.search(url)) and same_issuer(url,base) and
                    not re.search(r"\.(?:pdf|pptx?|docx?|xlsx?)(?:$|\?)",url,re.I)):
                event_url=url
        out.append(Candidate(company,title,day,start,event_url,webcast,base,
                             method+":dom",content[:400],missing))
        if iso_attr:
            visible_start,_,visible_issue=parse_start(content)
            if visible_issue and "conflict" in visible_issue:
                out[-1].conflicts.append(visible_issue)
            if visible_start and start and datetime.fromisoformat(visible_start)!=datetime.fromisoformat(start):
                out[-1].conflicts.append("visible_time_conflicts_with_datetime_attribute")
        # Add-to-calendar URLs contain explicit UTC timestamps even when the
        # visible card omits a timezone. Read the URL locally, never visit Google.
        for a in node.select("a[href]"):
            calendar=from_calendar_link(company,a.get("href",""),base,method,title)
            if calendar:
                calendar.event_date=day
                out.append(calendar)
    # Detail pages often have an h1 and date in separate blocks, rather than a
    # card. A bounded main-content extraction handles that layout.
    if DETAIL_RE.search(base) and not out:
        main=soup.select_one("main,[role='main'],.wd_event,.node--type-nir-event")
        heading=soup.select_one("h1")
        if main and heading and relevant(heading.get_text(" ",strip=True)):
            text=compact(main.get_text(" ",strip=True))[:2200]
            if len(set(DATE_RE.findall(text)))==1:
                start,day,missing=parse_start(text)
                if day:
                    web=next((clean_url(base,a["href"]) for a in main.select("a[href]")
                              if re.search(r"webcast|listen|watch|replay",a.get_text(),re.I)),None)
                    out.append(Candidate(company,compact(heading.get_text(" ",strip=True)),day,
                        start,base,web,base,method+":detail",text[:400],missing))
    return merge(out)


def from_calendar_link(company, link, base, method, title=None):
    """Parse only calendar parameters actually linked by the issuer."""
    u=urlsplit(urljoin(base,link))
    if u.hostname not in ("www.google.com","calendar.google.com") or "calendar" not in u.path:
        return None
    q=parse_qs(u.query)
    raw=q.get("dates",[""])[0].split("/")[0]
    if not re.fullmatch(r"\d{8}T\d{6}Z",raw):
        return None
    title=title or q.get("text",[""])[0]
    if not relevant(title):
        return None
    start=datetime.strptime(raw,"%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    details=q.get("details",[""])[0]
    event=re.search(r"Event Details:\s*(https?://\S+)",details,re.I)
    web=re.search(r"Webcast:\s*(https?://\S+)",details,re.I)
    event_url=clean_url(base,event.group(1)) if event else base
    if not same_issuer(event_url or base,base):
        event_url=base
    return Candidate(company,compact(title),start.date().isoformat(),start.isoformat(),
        event_url or base,clean_url(base,web.group(1)) if web else None,base,
        method+":calendar-link",details[:400],None)


def from_ical(company, text, base, listing):
    from icalendar import Calendar
    out=[]
    for event in Calendar.from_ical(text).walk("VEVENT")[:500]:
        title=str(event.get("summary",""))
        if not relevant(title) or str(event.get("status","")).upper()=="CANCELLED":
            continue
        stamp=event.get("dtstart")
        if stamp is None:
            continue
        stamp=stamp.dt
        aware=isinstance(stamp,datetime) and stamp.utcoffset() is not None
        event_url=clean_url(listing,str(event.get("url",""))) or listing
        web=None
        if not same_issuer(event_url,listing):
            web,event_url=event_url,listing
        desc=str(event.get("description",""))
        web_match=re.search(r"(?:webcast|watch|listen):?\s*(https?://\S+)",desc,re.I)
        web=clean_url(listing,web_match.group(1)) if web_match else web
        out.append(Candidate(company,compact(title),stamp.date().isoformat() if
            isinstance(stamp,datetime) else stamp.isoformat(),stamp.isoformat() if aware else None,
            event_url,web,listing,"http:linked-ical",desc[:400],
            None if aware else ("timezone_missing" if isinstance(stamp,datetime) else "time_missing")))
    return out


def xml_root(text):
    if len(text)>MAX_BODY or re.search(r"<!DOCTYPE|<!ENTITY",text,re.I):
        raise ValueError("Oversized XML or disallowed XML entity declaration")
    return ET.fromstring(text)


def from_feed(company, text, base):
    """Publication dates are NOT event dates; parse explicit dates in the copy."""
    root=xml_root(text)
    out=[]
    for item in list(root.iter()):
        if item.tag.rsplit("}",1)[-1] not in ("item","entry"):
            continue
        parts={n.tag.rsplit("}",1)[-1]:n for n in item}
        title=parts.get("title")
        title=title.text or "" if title is not None else ""
        if not relevant(title):
            continue
        link=parts.get("link")
        url=clean_url(base,link.get("href") or link.text) if link is not None else base
        desc=next((parts[k] for k in ("encoded","content","description","summary") if k in parts),None)
        if desc is not None:
            fragment=desc.text or ""
            out.extend(from_release(company,fragment,url or base,title))
    return out


def from_release(company, html, base, title=None):
    soup=BeautifulSoup(html,"html.parser")
    for n in soup.select("script,style,nav,header,footer"):
        n.decompose()
    h=soup.select_one("h1")
    title=title or (compact(h.get_text(" ",strip=True)) if h else "")
    if not relevant(title):
        return []
    out=[]
    # Earnings releases can contain hundreds of financial table cells before
    # their call details. Do not spend the paragraph budget on those cells.
    paragraphs=soup.select("p,li")[:1200]
    if not paragraphs and len(soup.get_text())<1200:
        paragraphs=[soup]
    previous=""
    web_links=[clean_url(base,a["href"]) for a in soup.select("a[href]")
               if re.search(r"webcast|watch|listen",a.get_text(),re.I)]
    for p in paragraphs:
        text=compact(p.get_text(" ",strip=True))
        # Announcements often put the scheduled date and call time in adjacent
        # paragraphs. Carry only an explicit 'on <date>' scheduling statement,
        # never a release timestamp or a fiscal period end.
        if not DATE_RE.search(text) and (TIME_RE.search(text) or H24_RE.search(text)):
            prior=list(DATE_RE.finditer(previous))
            if prior and re.search(r"conference call|webcast|presentation",text,re.I):
                chosen=prior[-1]
                prefix=previous[max(0,chosen.start()-45):chosen.start()]
                if re.search(r"\bon\s+(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?$",prefix,re.I):
                    text=chosen.group()+" "+text
        previous=compact(p.get_text(" ",strip=True))
        # An announcement's release timestamp alone can never become a call.
        if not DATE_RE.search(text) or not (TIME_RE.search(text) or H24_RE.search(text)):
            continue
        start,day,missing=parse_start(text)
        if day:
            web=next((clean_url(base,a["href"]) for a in p.select("a[href]")
                      if re.search(r"webcast|watch|listen",a.get_text(),re.I)),None)
            web=web or next((u for u in web_links if u and not same_issuer(u,base)),None)
            out.append(Candidate(company,compact(title),day,start,base,web,base,
                "http:issuer-announcement",text[:600],missing))
    return out


def merge(rows: list[Candidate]) -> list[Candidate]:
    bykey={}
    for item in rows:
        if not item.event_date:
            continue
        key=item.identity
        evidence={"url":item.listing_url,"method":item.extraction_method,
                  "start":item.start,"excerpt":item.excerpt}
        if evidence not in item.evidence:
            item.evidence.append(evidence)
        if key not in bykey:
            bykey[key]=item
        else:
            old=bykey[key]
            for issue in item.conflicts:
                if issue not in old.conflicts:
                    old.conflicts.append(issue)
            if old.start and item.start and datetime.fromisoformat(old.start)!=datetime.fromisoformat(item.start):
                if "conflicting_source_timestamps" not in old.conflicts:
                    old.conflicts.append("conflicting_source_timestamps")
            for proof in item.evidence:
                if proof not in old.evidence and len(old.evidence)<12:
                    old.evidence.append(proof)
            if not old.start and item.start:
                old.start,old.missing=item.start,item.missing
            if not old.webcast_url and item.webcast_url:
                old.webcast_url=item.webcast_url
            if old.missing is None and item.missing and "conflict" in item.missing:
                old.conflicts.append(item.missing)
            if old.event_url==old.listing_url and item.event_url!=item.listing_url:
                old.event_url=item.event_url
            if item.extraction_method not in old.extraction_method:
                old.extraction_method+=" + "+item.extraction_method
    return sorted(bykey.values(),key=lambda x:(x.event_date or "",x.company,x.title))




@dataclass
class FetchResult:
    url: str
    status: int | None = None
    text: str = ""
    headers: dict = field(default_factory=dict)
    error: str | None = None
    cache: str = "miss"


class PublicHTTP:
    """One connection pool, bounded downloads, revalidation and per-host pacing."""

    def __init__(self, client, cache_dir=None, concurrency=8, timeout=12):
        self.client=client
        self.cache_dir=cache_dir
        self.timeout=timeout
        self.slots=asyncio.Semaphore(concurrency)
        self.host_slots={}
        self.cooldown={}
        self.inflight={}
        self.requests=0
        self.revalidated=0
        if cache_dir and cache_dir.exists():
            # Public URLs with cache-busting query strings must not grow the
            # Actions cache without bound. Keep at most 32 MB / 200 recent files.
            total=0
            entries=[]
            for path in cache_dir.glob("*.json"):
                try:
                    stat=path.stat();entries.append((stat.st_mtime,stat.st_size,path))
                except OSError:
                    continue
            for i,(mtime,size,path) in enumerate(sorted(entries,reverse=True)):
                if i>=200 or total+size>32_000_000 or time.time()-mtime>7*86400:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                else:
                    total+=size

    async def fetch(self, url):
        if url not in self.inflight:
            task=asyncio.create_task(self._fetch(url))
            self.inflight[url]=task
        # A site's deadline must not cancel a request shared by another site.
        return await asyncio.shield(self.inflight[url])

    def _cache_path(self, url):
        return self.cache_dir/(hashlib.sha256(url.encode()).hexdigest()+".json") if self.cache_dir else None

    def _cached(self, url):
        path=self._cache_path(url)
        try:
            if path and time.time()-path.stat().st_mtime<7*86400 and path.stat().st_size<MAX_BODY*2:
                data=json.loads(path.read_text())
                if (data["url"]==url and isinstance(data["text"],str) and
                        len(data["text"])<=MAX_BODY and isinstance(data.get("headers"),dict)):
                    return data
        except (OSError,ValueError,KeyError,TypeError):
            pass
        return None

    async def _fetch(self, url):
        host=urlsplit(url).hostname or ""
        if self.cooldown.get(host,0)>time.monotonic():
            return FetchResult(url,429,error="host_rate_limited")
        cached=self._cached(url)
        headers={}
        if cached:
            for source,target in (("etag","If-None-Match"),("last-modified","If-Modified-Since")):
                if cached["headers"].get(source):
                    headers[target]=cached["headers"][source]
        host_slot=self.host_slots.setdefault(host,asyncio.Semaphore(2))
        async with self.slots, host_slot:
            if self.cooldown.get(host,0)>time.monotonic():
                return FetchResult(url,429,error="host_rate_limited")
            for attempt in range(2):
                try:
                    async with asyncio.timeout(self.timeout):
                        async with self.client.stream("GET",url,headers=headers) as response:
                            self.requests+=1
                            status=response.status_code
                            meta=dict(response.headers)
                            final=str(response.url)
                            if status==304:
                                if cached:
                                    self.revalidated+=1
                                    return FetchResult(final,304,cached["text"],cached["headers"],cache="revalidated")
                                headers={}
                                continue
                            if status==429:
                                self.cooldown[host]=time.monotonic()+retry_delay(meta.get("retry-after"),60)
                                return FetchResult(final,status,headers=meta,error="rate_limited")
                            if status in (408,500,502,503,504) and attempt==0:
                                delay=retry_delay(meta.get("retry-after"),0.4)
                                if delay<=3:
                                    await asyncio.sleep(delay)
                                    continue
                            if status>=400:
                                return FetchResult(final,status,headers=meta,error="http_"+str(status))
                            if int(meta.get("content-length","0") or "0")>MAX_BODY:
                                return FetchResult(final,status,headers=meta,error="body_too_large")
                            chunks=[];size=0
                            async for chunk in response.aiter_bytes():
                                size+=len(chunk)
                                if size>MAX_BODY:
                                    return FetchResult(final,status,headers=meta,error="body_too_large")
                                chunks.append(chunk)
                            raw=b"".join(chunks)
                            text=raw.decode(response.encoding or "utf-8",errors="replace")
                            result=FetchResult(final,status,text,meta)
                            # Never reuse a challenge/error page as fresh source evidence.
                            if is_challenge(text):
                                result.error="challenge_page"
                            elif (self.cache_dir and status==200 and
                                  (meta.get("etag") or meta.get("last-modified")) and
                                  "no-store" not in meta.get("cache-control","").lower()):
                                try:
                                    self.cache_dir.mkdir(parents=True,exist_ok=True)
                                    path=self._cache_path(url)
                                    keep={k:v for k,v in meta.items() if k in
                                          ("etag","last-modified","content-type")}
                                    payload={"url":url,"text":text,"headers":keep,"retrieved_at_utc":now()}
                                    tmp=path.with_suffix(".tmp")
                                    tmp.write_text(json.dumps(payload),encoding="utf-8")
                                    tmp.replace(path)
                                except OSError:
                                    pass  # Caching is an optimization, never a prerequisite.
                            return result
                except (httpx.HTTPError,TimeoutError,ValueError) as ex:
                    if attempt:
                        return FetchResult(url,error=type(ex).__name__+": "+str(ex)[:180])
                    await asyncio.sleep(0.25)
        return FetchResult(url,error="request_failed")

    async def close(self):
        pending=[t for t in self.inflight.values() if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending,return_exceptions=True)


def retry_delay(value, default):
    if value:
        try:
            return max(0,float(value))
        except ValueError:
            try:
                dt=parsedate_to_datetime(value)
                return max(0,(dt-datetime.now(timezone.utc)).total_seconds())
            except (ValueError,TypeError):
                pass
    return default


def is_challenge(html):
    # A newsletter form mentioning CAPTCHA is not a challenge page.
    if re.search(r"<(?:rss|feed)(?:\s|>)",html[:500],re.I) or not re.search(r"<html|<!doctype html|<title",html[:12000],re.I):
        return False
    soup=BeautifulSoup(html[:12000],"html.parser")
    title=compact(soup.title.get_text() if soup.title else "").lower()
    for n in soup.select("script,style"):
        n.decompose()
    body=compact(soup.get_text(" ",strip=True)).lower()
    return (any(s in title for s in BAD_WORDS) or
            (len(body)<1200 and any(s in body for s in
             ("verify you are human","access denied","checking your browser","just a moment"))))


def public_links(html, base):
    """Only URLs linked in retrieved issuer content; no guessed widget APIs."""
    if re.search(r"<(?:rss|feed)(?:\s|>)",html[:500],re.I) or not re.search(r"<html|<!doctype html|<a\b|<link\b",html[:12000],re.I):
        return {"feeds":[],"details":[],"archives":[]}
    soup=BeautifulSoup(html,"html.parser")
    links={"feeds":[],"details":[],"archives":[]}
    for a in soup.select("a[href],link[href]")[:3000]:
        url=clean_url(base,a.get("href"))
        if not url:
            continue
        label=compact(a.get_text(" ",strip=True))
        typ=str(a.get("type","")).lower()
        path=urlsplit(url).path.lower()
        if (typ in ("text/calendar","application/rss+xml","application/atom+xml") or
                re.search(r"\.(?:ics|vcs)(?:$|\?)",url,re.I)):
            links["feeds"].append(url)
        elif same_issuer(url,base):
            if re.search(r"rss|icalendar|icalfeed",url+" "+label,re.I):
                links["feeds"].append(url)
            elif url!=base and DETAIL_RE.search(url) and (relevant(label) or "event-details" in path):
                links["details"].append(url)
            elif "next" in a.get("rel",[]) or re.fullmatch(r"(next|next page|next ›)",label,re.I):
                links["archives"].append(url)
    return {k:list(dict.fromkeys(v)) for k,v in links.items()}


def parse_document(company, response, listing):
    text=response.text
    if text.lstrip().startswith("BEGIN:VCALENDAR"):
        return from_ical(company,text,response.url,listing)
    if text.lstrip().startswith(("{","[")):
        return from_json(company,json.loads(text),listing,"http:public-json")
    if re.search(r"<(?:rss|feed)(?:\s|>)",text[:1000],re.I):
        return from_feed(company,text,response.url)
    return from_html(company,text,response.url,"http:html")


def decode_public_json(body):
    """Decode JSON or an observed public JSONP response without executing it."""
    text=body.decode("utf-8-sig") if isinstance(body,bytes) else body.lstrip("\ufeff")
    try:
        return json.loads(text)
    except ValueError:
        wrapped=re.fullmatch(r"\s*[A-Za-z_$][\w.$]*\s*\((.*)\)\s*;?\s*",text,re.S)
        if not wrapped:
            raise ValueError("Not JSON or a public JSONP response")
        return json.loads(wrapped.group(1))


def record_fetch(log, result, purpose):
    log["http_attempts"].append({"url":result.url,"purpose":purpose,
        "status":result.status,"error":result.error,"cache":result.cache,
        "retrieved_at_utc":now(),"bytes":len(result.text.encode("utf-8"))})


async def http_sources(http, company, years, max_details, state):
    """Fast source discovery, linked-feed recovery and concurrent detail reads."""
    name=company["company"]
    log=state["log"];found=state["found"];documents=state["documents"]
    async def read(url,purpose,listing=None):
        response=await http.fetch(url)
        record_fetch(log,response,purpose)
        if not response.error and response.text:
            try:
                found.extend(parse_document(name,response,listing or response.url))
                documents[response.url]=response.text
            except (ValueError,TypeError,OverflowError,ET.ParseError) as ex:
                log["errors"].append(purpose+": "+type(ex).__name__+": "+str(ex)[:150])
        return response

    for url in company["event_urls"][:3]:
        response=await read(url,"investor_calendar")
        if response.text and not response.error:
            state["primary_readable"]=True
            # A useful static listing saves all browser startup/rendering work.
            if found:
                break
    links={"feeds":[],"details":[],"archives":[]}
    for url,html in list(documents.items()):
        for kind,urls in public_links(html,url).items():
            links[kind].extend(urls)
    # Follow actual next-page links once, and prefer explicit event calendars.
    for url in list(dict.fromkeys(links["archives"]))[:1]:
        await read(url,"archive_next")
    for url in list(dict.fromkeys(links["feeds"]))[:3]:
        response=await read(url,"linked_feed",company["event_urls"][0])
        # RSS landing pages can link to the actual public event RSS/ICS feeds.
        if response.text and not response.error and "<html" in response.text[:1000].lower():
            more=public_links(response.text,response.url)["feeds"]
            actual=[u for u in more if u!=url and re.search(r"\.(xml|ics)|/rss/|rss\.ashx",u,re.I)]
            await asyncio.gather(*(read(u,"linked_feed",company["event_urls"][0]) for u in actual[:3]))
    for url,html in list(documents.items()):
        links["details"].extend(public_links(html,url)["details"])
    clean=merge([e for e in found if e.event_date and int(e.event_date[:4]) in years])
    needs={e.event_url for e in clean if not e.review_ready}
    linked=list(dict.fromkeys(links["details"]))
    # Missing-date cards still lead to real detail pages, even when no initial
    # Candidate could be parsed. Complete existing cards need no extra request.
    complete={e.event_url for e in clean if e.review_ready}
    detail_urls=[u for u in linked if u not in complete]
    detail_urls.sort(key=lambda u:(u not in needs,u not in linked[:5]))
    async def detail(url):
        response=await read(url,"issuer_detail")
        if response.text and not response.error:
            log["detail_pages"]+=1
    await asyncio.gather(*(detail(u) for u in detail_urls[:max_details]))

    # A blocked/empty IR calendar can still have readable corporate press pages.
    # This is public-company fallback discovery; private companies remain excluded.
    if not merge(found):
        for url in company["news_urls"][:2]:
            response=await http.fetch(url)
            record_fetch(log,response,"public_news_fallback")
            if response.error:
                continue
            soup=BeautifulSoup(response.text,"html.parser")
            for nav in soup.select("nav,header,footer,[role='navigation']"):
                nav.decompose()
            release_links=[]
            for a in soup.select("a[href]"):
                link=clean_url(response.url,a.get("href"))
                label=compact(a.get_text(" ",strip=True))
                if (link and relevant(label) and
                        re.search(r"news-release|press-release|news/|news-details|/20\d\d[-/]",link,re.I)
                        and any(same_issuer(link,u) for u in company["news_urls"])):
                    release_links.append((link,label))
            async def announcement(link,title):
                res=await http.fetch(link)
                record_fetch(log,res,"issuer_announcement")
                if not res.error:
                    found.extend(from_release(name,res.text,res.url,title))
            await asyncio.gather(*(announcement(u,t) for u,t in
                list(dict.fromkeys(release_links))[:max_details]))
            if found:
                break


async def scan_company(browser, sem, company: dict, years: list[int], timeout_ms: int, max_details: int, http=None, seed=None, progress=None):
    from playwright.async_api import Error as PWError
    name=company["company"]; url=company["event_urls"][0]
    log={"company":name,"primary_url":url,"mode":company["mode"],
         "checked_at_utc":now(),"status":"NOT_ATTEMPTED","errors":[],
         "views":[],"network_event_payloads":0,"detail_pages":0,
         "script_errors":[],"failed_requests":[],"resource_failures":[],"observed_event_requests":[]}
    found=progress["found"] if progress is not None else []
    if progress is not None:
        progress["browser_log"]=log
    async with sem:
        ctx=await browser.new_context(locale="en-US",viewport={"width":1280,"height":850},
                                      service_workers="block")
        page=await ctx.new_page()
        page.on("pageerror",lambda ex: log["script_errors"].append(str(ex)[:200])
                if len(log["script_errors"])<12 else None)
        page.on("requestfailed",lambda req: log["failed_requests"].append(
                {"url":req.url,"error":req.failure}) if len(log["failed_requests"])<20 else None)
        pending=[]
        if http is not None:
            observed_scripts=set()
            if seed and seed.text:
                soup=BeautifulSoup(seed.text,"html.parser")
                script_urls=list(dict.fromkeys(clean_url(url,s["src"]) for s in soup.select("script[src]")))
                observed_scripts=set(script_urls)
                critical=[u for u in script_urls if u and not re.search(
                    r"googletagmanager|google-analytics|connect\.facebook|doubleclick|recaptcha",u,re.I)]
                try:
                    async with asyncio.timeout(12):
                        await asyncio.gather(*(http.fetch(u) for u in critical[:40]))
                except TimeoutError:
                    log["errors"].append("Script prefetch budget reached; rendering continues")
            async def route_public(route):
                request=route.request
                # External CSS can block synchronous widget scripts for tens of
                # seconds. DOM extraction needs neither stylesheets nor media.
                if request.resource_type in ("image","media","font","stylesheet"):
                    await route.abort();return
                if request.resource_type=="script" and re.search(
                    r"googletagmanager|google-analytics|connect\.facebook|doubleclick|recaptcha",request.url,re.I):
                    await route.abort();return
                # Serve the issuer's successfully retrieved HTML under its real
                # origin so the site's own JavaScript can render normally.
                if request.method=="GET" and (same_issuer(request.url,url) or
                        request.url in observed_scripts):
                    result=await http.fetch(request.url)
                    if not result.error and result.text:
                        await route.fulfill(status=200 if result.status==304 else result.status,
                            headers={"content-type":result.headers.get("content-type","text/html")},
                            body=result.text)
                        return
                    if result.status==429:
                        await route.abort();return
                    if len(log["resource_failures"])<30:
                        log["resource_failures"].append({"url":request.url,"status":result.status,
                                                       "error":result.error})
                await route.continue_()
            await ctx.route("**/*",route_public)
        def on_response(resp):
            if resp.request.resource_type in ("xhr","fetch") and len(log["observed_event_requests"])<40:
                log["observed_event_requests"].append({"url":resp.url,"status":resp.status,
                    "method":resp.request.method})
            if (resp.status==200 and ("json" in resp.headers.get("content-type","").lower() or
                re.search(r"/feed/[^?]*event",resp.url,re.I))
                and (same_issuer(resp.url,url) or any(
                    (urlsplit(resp.url).hostname or "")==vendor or
                    (urlsplit(resp.url).hostname or "").endswith("."+vendor)
                    for vendor in ("q4inc.com","gcs-web.com","q4web.com")))):
                async def extract_response():
                    try:
                        body=await resp.body()
                        if len(body)>MAX_BODY:
                            return
                        extracted=from_json(name,decode_public_json(body),url,"browser:observed-public-response")
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
            # Do not let a slow analytics script prevent reading an already
            # committed document and its independently rendered event widget.
            response=await page.goto(url,wait_until="commit",timeout=timeout_ms)
            status=response.status if response else None
            log["http_status"]=status
            log["final_url"]=page.url
            if status in (401,403,429):
                log["status"]="ACCESS_RESTRICTED"
            elif status is None or status>=400:
                log["status"]="HTTP_ERROR"
            else:
                try:
                    await page.wait_for_function("""() => Array.from(document.querySelectorAll(
                      'article, .module_item, .evergreen-event-item, .views-row, .uk-card'
                    )).some(n => /20[0-9]{2}/.test(n.innerText))""",timeout=min(9000,timeout_ms))
                except PWError:
                    pass
                if is_challenge(await page.content()):
                    log["status"]="CHALLENGE_PAGE"
                else:
                    initial_count=len(found)
                    await capture("initial")
                    if len(found)==initial_count:
                        # Recover broken issuer widget initialization using the
                        # issuer's own already-loaded library, explicitly in its
                        # PUBLIC mode. No endpoint, API key or event fact is guessed.
                        widget=await page.evaluate("""(years) => {
                          const jq=window.$tudio || window.jQuery;
                          if (!jq || !jq.fn) return 'library_unavailable';
                          const plugin=jq.fn.q4Events ? 'q4Events' : jq.fn.events ? 'events' : null;
                          if (!plugin) return 'widget_unavailable';
                          try {
                            const node=document.createElement('div');
                            node.id='oncodiag-public-event-discovery';
                            document.body.appendChild(node);
                            jq(node)[plugin]({forcePublic:true, url:window.location.origin,
                              showPast:true, showFuture:true, limit:200, years:years,
                              startYear:new Date().getFullYear(), forceStartYear:true,
                              itemTemplate:'', template:'', useMoment:true});
                            return plugin;
                          } catch (e) { return 'error: '+String(e).slice(0,150); }
                        }""",years)
                        log["public_widget_fallback"]=widget
                        if widget in ("q4Events","events"):
                            until=time.monotonic()+8
                            while len(found)==initial_count and time.monotonic()<until:
                                await asyncio.sleep(0.1)
                            await capture("public-widget-recovery")
                    for _ in range(2):
                        await page.mouse.wheel(0,1100)
                        await page.wait_for_timeout(280)
                    await capture("after_scroll")
                    log["ready_state"]=await page.evaluate("document.readyState")
                    log["script_context"]=await page.evaluate("""() => ({
                        jquery: typeof window.jQuery, q4: typeof window.q4App,
                        studio: typeof window.$tudio, scripts: document.scripts.length
                    })""")
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
                              and not e.review_ready))
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
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending,return_exceptions=True)
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
    if (len(companies)!=25 or sum(x["kind"]=="public" for x in companies)!=23 or
            sum(x["kind"]=="private" for x in companies)!=2 or
            len({x["company"] for x in companies})!=25):
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
        requested={n.lower() for n in args.company}
        companies=[c for c in companies if c["company"].lower() in requested]
        if requested!={c["company"].lower() for c in companies}:
            raise ValueError("Unknown or out-of-scope company")
    years=[int(x) for x in args.years.split(",")]
    started=time.monotonic()
    report={"schema_version":1,"extractor_version":2,"generated_at_utc":now(),
            "extractor":"HTTP-first public sources + selective Chromium",
            "review_required":True,"events":[],"companies":{},"errors":[],
            "notes":["All extracted events require ChatGPT corroboration; no canonical JSON or ICS is written.",
                     "Readable, extracted and verified are distinct stages; zero candidates is not no events.",
                     "Private companies, SEC filings and scientific conference discovery are not scanned.",
                     "HTTP 304 revalidates a cached body; network failures never silently reuse stale evidence."]}
    company_slots=asyncio.Semaphore(args.concurrency)
    browser_slots=asyncio.Semaphore(args.browser_concurrency)
    limits=httpx.Limits(max_connections=12,max_keepalive_connections=10)
    async with httpx.AsyncClient(headers={"User-Agent":USER_AGENT,
             "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Accept-Language":"en-US,en;q=0.9"},follow_redirects=True,
             limits=limits,timeout=httpx.Timeout(12,connect=6)) as client:
        http=PublicHTTP(client,args.cache_dir,concurrency=10,
                        timeout=min(15,args.timeout_ms/1000))
        async with async_playwright() as p:
            browser=None
            browser_error=None
            browser_lock=asyncio.Lock()
            async def get_browser():
                nonlocal browser,browser_error
                async with browser_lock:
                    if browser is None and browser_error is None:
                        try:
                            browser=await p.chromium.launch(headless=True)
                        except Exception as ex:
                            browser_error=type(ex).__name__+": "+str(ex)[:250]
                return browser

            async def protected(company):
                # Queueing never consumes an issuer's end-to-end time budget.
                async with company_slots:
                    began=time.monotonic()
                    log={"company":company["company"],"primary_url":company["event_urls"][0],
                         "mode":company["mode"],"checked_at_utc":now(),"status":"NOT_ATTEMPTED",
                         "http_attempts":[],"errors":[],"detail_pages":0,
                         "browser_status":"NOT_NEEDED","coverage":"DISCOVERY_ONLY"}
                    state={"log":log,"found":[],"documents":{},"primary_readable":False}
                    try:
                        async with asyncio.timeout(args.site_deadline):
                            try:
                                async with asyncio.timeout(min(55,args.site_deadline*0.6)):
                                    await http_sources(http,company,years,args.max_details,state)
                            except TimeoutError:
                                log["errors"].append("HTTP discovery budget exhausted; preserved partial candidates")
                            dynamic=any(re.search(r"\.q4Events\s*\(|selectEvergreenEventYear",
                                        html,re.I) for html in state["documents"].values())
                            if dynamic or not state["found"]:
                                if args.http_only:
                                    log["browser_status"]="DISABLED"
                                else:
                                    # Wait for a browser slot outside its own render budget,
                                    # but inside the whole-company deadline.
                                    async with browser_slots:
                                        b=await get_browser()
                                        if b is None:
                                            log["browser_status"]="UNAVAILABLE"
                                            log["errors"].append(browser_error)
                                        else:
                                            url=company["event_urls"][0]
                                            cached=await http.fetch(url)
                                            log["browser_status"]="ATTEMPTED"
                                            await scan_company(b,asyncio.Semaphore(1),company,years,
                                                args.timeout_ms,args.max_details,http,cached,state)
                                            log["browser_status"]=state["browser_log"]["status"]
                    except TimeoutError:
                        log["errors"].append("Company deadline exhausted; preserved partial candidates")
                    except Exception as ex:
                        log["errors"].append(type(ex).__name__+": "+str(ex)[:250])
                    rows=merge([e for e in state["found"] if e.event_date and
                                int(e.event_date[:4]) in years])
                    log["candidate_count"]=len(rows)
                    log["review_ready_count"]=sum(e.review_ready for e in rows)
                    log["conflicted_candidate_count"]=sum(bool(e.conflicts) or
                        bool(e.missing and "conflict" in e.missing) for e in rows)
                    log["primary_readable"]=state["primary_readable"]
                    log["primary_access_errors"]=[x for x in log["http_attempts"]
                        if x["purpose"]=="investor_calendar" and x["error"]]
                    log["recovered_after_primary_failure"]=bool(rows and log["primary_access_errors"])
                    log["status"]="CANDIDATES_FOUND" if rows else (
                        "NO_EVENT_CANDIDATES_NOT_VERIFIED" if state["primary_readable"] else "ACCESS_RESTRICTED")
                    if company["mode"]=="unconfirmed":
                        log["warning"]="MGI: No issuer-owned dated investor event calendar independently confirmed."
                    if state.get("browser_log"):
                        log["browser"]=state["browser_log"]
                    log["elapsed_seconds"]=round(time.monotonic()-began,3)
                    report["companies"][company["company"]]=log
                    report["events"].extend(asdict(e)|{"id":e.identity,
                        "ready_for_manual_review":e.review_ready} for e in rows)
                    print(json.dumps({"company":company["company"],"candidates":len(rows),
                        "browser":log["browser_status"],"seconds":log["elapsed_seconds"]}),flush=True)

            try:
                await asyncio.gather(*(protected(c) for c in companies))
            finally:
                await http.close()
                if browser:
                    await browser.close()
        report["http_requests"]=http.requests
        report["cache_revalidations"]=http.revalidated
    report["events"].sort(key=lambda e:(e["company"],e["event_date"],e["title"]))
    report["completed_at_utc"]=now()
    report["summary"]={
        "public_companies_checked":len(report["companies"]),
        "companies_with_candidates":sum(v["candidate_count"]>0 for v in report["companies"].values()),
        "event_candidates":len(report["events"]),
        "sites_without_candidates":sum(v["candidate_count"]==0 for v in report["companies"].values()),
        "sites_with_access_errors":sum(bool(v["primary_access_errors"]) or
            v["browser_status"] in ("ACCESS_RESTRICTED","CHALLENGE_PAGE","BROWSER_ERROR","UNAVAILABLE")
            for v in report["companies"].values()),
        "sites_recovered_after_primary_failure":sum(v["recovered_after_primary_failure"]
            for v in report["companies"].values()),
        "elapsed_seconds":round(time.monotonic()-started,3),
        "source_verification":"NOT_PERFORMED_REQUIRES_CHATGPT"}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temp=args.output.with_suffix(args.output.suffix+".tmp")
    temp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(report["summary"],indent=2))
    return 2 if not any(v["primary_readable"] or v["candidate_count"]
                       for v in report["companies"].values()) else 0


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


async def browser_smoke_test():
    """Exercise Chromium/JavaScript without accessing an external website."""
    from playwright.async_api import async_playwright
    html = """<div id="events"></div>
<script>
setTimeout(function() {
  document.getElementById('events').innerHTML =
    '<article><h3>Q3 Earnings Conference Call</h3>' +
    '<time datetime="2026-11-05T16:30:00-05:00"></time>' +
    '<a href="/events/q3">Q3 Earnings Conference Call</a>' +
    '<a href="https://stream.example/q3">Listen to webcast</a></article>';
}, 50);
</script>"""
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        try:
            page=await browser.new_page()
            await page.goto("about:blank")
            await page.set_content(html)
            await page.wait_for_selector("article",timeout=5000)
            rows=from_html("TestCo",await page.content(),
                           "https://investor.company.example/events","smoke:javascript")
            if len(rows)!=1 or not rows[0].review_ready:
                raise AssertionError("Chromium rendered event not captured")
            await page.close()
            # Broken page bootstrap: the issuer library exists, but never starts
            # its event widget. Verify public-mode recovery and intercepted XHR
            # without accessing any external service.
            widget_html="""<html><head>
<link rel="stylesheet" href="https://unreachable.example/slow.css">
</head><body><div>Investor events are loading</div><script>
window.$tudio=function(node) { return {q4Events:function(options) {
  if (!options.forcePublic) throw new Error('Public mode is required');
  fetch('/feed/Event.svc/GetEventList').then(r=>r.json()).then(data=>window.events=data);
}}; };
window.$tudio.fn={q4Events:function(){}};
</script></body></html>"""
            seen=[]
            async def mocked(request):
                seen.append(request.url.path)
                if request.url.path=="/events":
                    return httpx.Response(200,text=widget_html,headers={"content-type":"text/html"})
                if request.url.path=="/feed/Event.svc/GetEventList":
                    return httpx.Response(200,json={"items":[{
                        "eventName":"Q4 Earnings Conference Call",
                        "startDate":"2026-11-07T08:00:00-05:00",
                        "eventUrl":"/events/q4"}]})
                raise AssertionError("Unexpected external request: "+str(request.url))
            async with httpx.AsyncClient(transport=httpx.MockTransport(mocked)) as client:
                http=PublicHTTP(client)
                url="https://issuer.example/events"
                seed=await http.fetch(url)
                state={"found":[]}
                recovered,log=await scan_company(browser,asyncio.Semaphore(1),
                    {"company":"TestCo","mode":"dynamic","event_urls":[url]},
                    [2026],1500,0,http,seed,state)
                if len(recovered)!=1 or not recovered[0].review_ready:
                    raise AssertionError("Public widget/XHR recovery failed: "+str(log))
                if "/feed/Event.svc/GetEventList" not in seen or log.get("public_widget_fallback")!="q4Events":
                    raise AssertionError("Issuer public widget did not execute")
                await http.close()
        finally:
            await browser.close()
    print("Chromium smoke: DOM rendering, HTTP replay, stylesheet skipping and public-widget/XHR recovery passed")


def cli():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=Path("earnings/config/sources.json"))
    parser.add_argument("--output",type=Path,default=Path("earnings/extractions/latest.json"))
    parser.add_argument("--company",action="append")
    parser.add_argument("--years",default=",".join(map(str,[date.today().year-1,date.today().year,
                                                        date.today().year+1])))
    parser.add_argument("--concurrency",type=int,default=6)
    parser.add_argument("--browser-concurrency",type=int,default=2)
    parser.add_argument("--cache-dir",type=Path)
    parser.add_argument("--http-only",action="store_true",help="Skip optional Chromium rendering")
    parser.add_argument("--timeout-ms",type=int,default=22000)
    parser.add_argument("--max-details",type=int,default=10)
    parser.add_argument("--site-deadline",type=int,default=130)
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--browser-smoke",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test();return 0
    if args.browser_smoke:
        asyncio.run(browser_smoke_test());return 0
    if not 1<=args.concurrency<=12 or not 1<=args.browser_concurrency<=3:
        parser.error("Company concurrency must be 1..12 and browser concurrency 1..3")
    if not 1<=args.max_details<=30 or not 10<=args.site_deadline<=300 or not 1000<=args.timeout_ms<=60000:
        parser.error("Invalid detail count, site deadline or navigation timeout")
    return asyncio.run(run(args))


if __name__=="__main__":
    raise SystemExit(cli())

