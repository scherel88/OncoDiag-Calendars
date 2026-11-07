#!/usr/bin/env python3
"""Validate conference source independently and compare it with the published ICS."""
import argparse
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "events.json"
FEED = ROOT / "upcoming-conferences.ics"

def escape_ics(value):
    return (str(value).replace("\\", "\\\\").replace("\r\n", "\n")
            .replace("\r", "\n").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))

def validate_source():
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported conference JSON schema")
    events = data.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("Conference event list is empty or missing")
    seen_uid, seen_edition = set(), set()
    for e in events:
        for key in ("uid", "title", "start_date", "end_date", "location", "url", "description"):
            if not isinstance(e.get(key), str) or not e[key].strip():
                raise ValueError(f"Conference record missing {key}: {e!r}")
        uid = e["uid"]
        if any(c in uid for c in "\r\n;, ") or uid in seen_uid:
            raise ValueError(f"Invalid or duplicate UID {uid!r}")
        seen_uid.add(uid)
        start, end = date.fromisoformat(e["start_date"]), date.fromisoformat(e["end_date"])
        if end <= start:
            raise ValueError(f"Invalid exclusive end date for {uid}")
        if not e["url"].startswith("https://") or "\n" in e["url"] or "\r" in e["url"]:
            raise ValueError(f"Invalid conference source URL for {uid}")
        edition = (e["title"], e["start_date"])
        if edition in seen_edition:
            raise ValueError(f"Duplicate title and date: {edition}")
        seen_edition.add(edition)
    return events

def validate_ics(events):
    content = FEED.read_text(encoding="utf-8")
    unfolded = re.sub(r"\r?\n[ \t]", "", content).replace("\r\n", "\n")
    if not unfolded.startswith("BEGIN:VCALENDAR\n") or not unfolded.rstrip().endswith("END:VCALENDAR"):
        raise ValueError("Malformed VCALENDAR")
    blocks = re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", unfolded, flags=re.S)
    if len(blocks) != len(events):
        raise ValueError(f"Feed has {len(blocks)} entries, source has {len(events)}")
    published = {}
    for block in blocks:
        props = dict(line.split(":", 1) for line in block.split("\n") if ":" in line)
        uid = props.get("UID")
        if not uid or uid in published:
            raise ValueError("Missing or duplicate published conference UID")
        published[uid] = props
    for e in events:
        uid = e["uid"]
        p = published.get(uid)
        if not p:
            raise ValueError(f"Missing published conference: {uid}")
        expected = {
            "SUMMARY": escape_ics(e["title"]),
            "DTSTART;VALUE=DATE": e["start_date"].replace("-", ""),
            "DTEND;VALUE=DATE": e["end_date"].replace("-", ""),
            "LOCATION": escape_ics(e["location"]),
            "URL": e["url"],
            "DESCRIPTION": escape_ics(e["description"]),
            "STATUS": "CONFIRMED",
            "TRANSP": "TRANSPARENT",
        }
        for key, value in expected.items():
            if p.get(key) != value:
                raise ValueError(f"{uid}: published {key} does not match source")
        if not p.get("DTSTAMP"):
            raise ValueError(f"{uid}: DTSTAMP missing")
    print(f"Validated {len(events)} published conference events")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ics", action="store_true")
    args = parser.parse_args()
    events = validate_source()
    print(f"Validated {len(events)} external conference records")
    if args.ics:
        validate_ics(events)

if __name__ == "__main__":
    main()
