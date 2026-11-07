#!/usr/bin/env python3
"""Generate conferences/upcoming-conferences.ics using only external events.json."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "events.json"
FEED = ROOT / "upcoming-conferences.ics"

def escape_ics(value):
    return (str(value).replace("\\", "\\\\").replace("\r\n", "\n")
            .replace("\r", "\n").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))

def fold_line(line):
    raw = line.encode("utf-8")
    chunks = []
    first = True
    while raw:
        limit = 75 if first else 74
        cut = min(len(raw), limit)
        while cut and cut < len(raw) and raw[cut] & 0xC0 == 0x80:
            cut -= 1
        if cut == 0:
            raise ValueError("Cannot fold UTF-8 calendar line")
        chunks.append(("" if first else " ") + raw[:cut].decode("utf-8"))
        raw = raw[cut:]
        first = False
    return "\r\n".join(chunks)

def main():
    items = json.loads(SOURCE.read_text(encoding="utf-8"))["events"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Conference Watch//Conference Calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Scientific and Investor Conferences",
        "X-WR-CALDESC:Published future conference dates tracked from official sources",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for e in sorted(items, key=lambda e: (e["start_date"], e["uid"])):
        lines.extend([
            "BEGIN:VEVENT",
            "UID:" + e["uid"],
            "DTSTAMP:" + stamp,
            "LAST-MODIFIED:" + stamp,
            "DTSTART;VALUE=DATE:" + e["start_date"].replace("-", ""),
            "DTEND;VALUE=DATE:" + e["end_date"].replace("-", ""),
            "SUMMARY:" + escape_ics(e["title"]),
            "LOCATION:" + escape_ics(e["location"]),
            "URL:" + e["url"],
            "DESCRIPTION:" + escape_ics(e["description"]),
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    data = "\r\n".join(fold_line(x) for x in lines) + "\r\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=ROOT,
            prefix=".conferences-", suffix=".tmp", delete=False
        ) as out:
            tmp = Path(out.name)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, FEED)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
