#!/usr/bin/env python3
"""Schema check only. Conference discovery belongs exclusively to ChatGPT."""
import json
from pathlib import Path
from urllib.parse import urlsplit

p=Path(__file__).with_name("source_registry.json")
data=json.loads(p.read_text(encoding="utf-8"))
if data.get("schema_version")!=1:
    raise SystemExit("Invalid source registry version")
rows=data.get("sources")
if not isinstance(rows,list) or len(rows)<30:
    raise SystemExit("At least 30 organizer/corroboration sources required")
hosts=set()
for row in rows:
    if not all(row.get(k) for k in ("organization","segment","url","tier")):
        raise SystemExit("Missing required website metadata")
    parsed=urlsplit(row["url"])
    if parsed.scheme!="https" or not parsed.hostname:
        raise SystemExit("Only HTTPS public website links permitted")
    if parsed.hostname in hosts:
        raise SystemExit("Duplicate source host: "+parsed.hostname)
    hosts.add(parsed.hostname)
print(f"Registry: {len(rows)} separate websites; STATIC CHECK ONLY (not live access).")
