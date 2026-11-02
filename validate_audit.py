#!/usr/bin/env python3
"""Validate the latest daily 26-company x 3-category investor-source audit."""
import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPANIES = (
    "Labcorp", "Mendelics", "Exact Sciences", "Veracyte", "Caris Life Sciences",
    "Guardant Health", "Myriad Genetics", "Natera", "Quest Diagnostics",
    "NeoGenomics", "DELFI Diagnostics", "Personalis", "GRAIL", "Tempus AI",
    "Adaptive Biotechnologies", "BillionToOne", "GeneDx", "SOPHiA GENETICS",
    "Illumina", "PacBio", "Oxford Nanopore Technologies", "Element Biosciences",
    "MGI Tech", "Thermo Fisher Scientific", "10x Genomics", "Twist Bioscience",
)
CATEGORIES = (
    "official_ir_events_webcasts",
    "corporate_ir_press_releases_investor_conferences",
    "official_filings_financial_reports",
)
STATUSES = {"verified", "not_applicable", "incomplete"}


def validate(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if path.stem != data.get("audit_date"):
        raise ValueError("Audit date does not match file name")
    checks = data.get("checks")
    if not isinstance(checks, list) or len(checks) != 78:
        raise ValueError("Expected exactly 78 independently recorded checks")
    expected = {(company, category) for company in COMPANIES for category in CATEGORIES}
    keys = [(c.get("company"), c.get("category")) for c in checks]
    if set(keys) != expected or len(set(keys)) != 78:
        raise ValueError("Missing, duplicated or unrecognized company/category checks")
    counts = Counter()
    for check in checks:
        name = f"{check['company']}/{check['category']}"
        status = check.get("status")
        if status not in STATUSES or check.get("attempted") is not True:
            raise ValueError(f"{name}: invalid status or missing attempt")
        stamp = check.get("checked_at", "")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError("timestamp lacks offset")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{name}: invalid checked_at {stamp}") from exc
        url = check.get("source_url", "")
        attempts = check.get("attempted_urls", [])
        if not url.startswith("https://") or not isinstance(attempts, list) or url not in attempts:
            raise ValueError(f"{name}: source URL missing from attempted URLs")
        if status == "verified":
            evidence = check.get("evidence_urls", [])
            if not evidence or not all(u.startswith("https://") for u in evidence):
                raise ValueError(f"{name}: verified without primary-source evidence URLs")
        if status == "not_applicable" and not check.get("rationale"):
            raise ValueError(f"{name}: N/A without documented rationale")
        if status == "incomplete":
            if not check.get("error") or check.get("retry_required") is not True:
                raise ValueError(f"{name}: incomplete without error and retry flag")
        counts[status] += 1
    if dict(counts) != {k: data.get("counts", {}).get(k, 0) for k in STATUSES}:
        raise ValueError("Recorded completion counts do not match check records")
    if data.get("company_count") != 26 or data.get("category_count") != 3 or data.get("total_checks") != 78:
        raise ValueError("Incorrect audit dimension metadata")
    if data.get("comprehensive") is True and counts["incomplete"]:
        raise ValueError("Cannot claim comprehensive audit with incomplete checks")
    print(f"{path}: {counts['verified']} verified, {counts['not_applicable']} N/A, "
          f"{counts['incomplete']} incomplete (78 recorded)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", action="store_true", help="Validate latest dated daily audit")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    if args.latest:
        paths = sorted(p for p in (ROOT / "audit").glob("????-??-??.json")
                       if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
        if not paths:
            raise ValueError("No daily audits found")
        validate(paths[-1])
    elif args.paths:
        for path in args.paths:
            validate(path)
    else:
        parser.error("Provide --latest or one or more audit file paths")


if __name__ == "__main__":
    main()
