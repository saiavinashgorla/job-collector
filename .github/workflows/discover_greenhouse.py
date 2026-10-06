#!/usr/bin/env python3
"""
Discover and validate Greenhouse board tokens for employers already classified
as Greenhouse in config/employers.csv.

Safety:
- Never overwrites config/employers.csv.
- Direct-source discoveries are marked DIRECT.
- API-token guesses are marked PROBE and remain unverified for company identity.
- Writes a staging CSV plus a JSON report.

Usage:
  python src/discover_greenhouse.py config/employers.csv --limit 10
  python src/discover_greenhouse.py config/employers.csv --write output/employers_greenhouse_staged.csv
"""

import argparse
import csv
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/plain,*/*",
}
TIMEOUT = 20
API_ROOT = "https://boards-api.greenhouse.io/v1/boards"
GH_URL_RE = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)",
    re.I,
)
GH_API_RE = re.compile(
    r"https?://boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)",
    re.I,
)


def get(url, **kwargs):
    try:
        return requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
            **kwargs,
        )
    except requests.RequestException:
        return None


def company_slug_variants(company):
    base = company.lower()

    replacements = {
        "&": " and ",
        "+": " ",
        "/": " ",
        ".": " ",
        "'": "",
        "’": "",
    }
    for old, new in replacements.items():
        base = base.replace(old, new)

    words = re.findall(r"[a-z0-9]+", base)

    removable_suffixes = {
        "inc", "corp", "corporation", "company", "co",
        "technologies", "technology", "holdings", "group",
        "systems", "software", "health", "financial",
    }

    variants = []

    def add(value):
        value = re.sub(r"[^a-z0-9_-]", "", value.lower())
        if value and value not in variants:
            variants.append(value)

    add("".join(words))
    add("-".join(words))
    add("_".join(words))

    trimmed = list(words)
    while trimmed and trimmed[-1] in removable_suffixes:
        trimmed.pop()

    if trimmed and trimmed != words:
        add("".join(trimmed))
        add("-".join(trimmed))
        add("_".join(trimmed))

    # Common brand alternatives from punctuation/slash labels.
    if "/" in company:
        for part in company.split("/"):
            part_words = re.findall(r"[a-z0-9]+", part.lower())
            add("".join(part_words))
            add("-".join(part_words))

    return variants[:12]


def extract_greenhouse_tokens(text):
    if not text:
        return []

    decoded = html.unescape(text).replace("\\/", "/")
    tokens = []

    for pattern in (GH_URL_RE, GH_API_RE):
        for match in pattern.findall(decoded):
            token = match.strip()
            if token and token not in tokens:
                tokens.append(token)

    return tokens


def candidate_source_urls(row):
    urls = []
    careers_url = (row.get("careers_url") or "").strip()

    if careers_url:
        urls.append(careers_url)

    notes = row.get("notes") or ""
    for url in re.findall(r"https?://[^\s,\"']+", notes):
        urls.append(url.rstrip(").;"))

    # Only add conservative guesses when no usable URL exists.
    if not urls:
        company = row["company"]
        words = re.findall(r"[a-z0-9]+", company.lower())
        if words:
            slug = "".join(words)
            urls.extend(
                [
                    f"https://www.{slug}.com/careers",
                    f"https://careers.{slug}.com/",
                    f"https://jobs.{slug}.com/",
                ]
            )

    seen = set()
    clean = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            clean.append(url)

    return clean[:5]


def validate_board(token):
    url = f"{API_ROOT}/{token}/jobs"
    response = get(url, params={"content": "false"})

    if response is None:
        return None

    if response.status_code != 200:
        return {
            "valid": False,
            "http_status": response.status_code,
            "jobs": None,
            "sample_title": None,
            "sample_location": None,
            "api_url": response.url,
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "valid": False,
            "http_status": 200,
            "jobs": None,
            "sample_title": None,
            "sample_location": None,
            "api_url": response.url,
        }

    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return {
            "valid": False,
            "http_status": 200,
            "jobs": None,
            "sample_title": None,
            "sample_location": None,
            "api_url": response.url,
        }

    sample = jobs[0] if jobs else {}
    location = sample.get("location") or {}
    if isinstance(location, dict):
        location = location.get("name")

    return {
        "valid": True,
        "http_status": 200,
        "jobs": len(jobs),
        "sample_title": sample.get("title"),
        "sample_location": location,
        "api_url": response.url,
    }


def discover_direct(row):
    for source_url in candidate_source_urls(row):
        response = get(source_url)

        if response is None:
            continue

        blobs = [
            response.url or "",
            response.text or "",
        ]

        tokens = []
        for blob in blobs:
            for token in extract_greenhouse_tokens(blob):
                if token not in tokens:
                    tokens.append(token)

        for token in tokens:
            validation = validate_board(token)
            if validation and validation["valid"]:
                return {
                    "method": "DIRECT",
                    "source_url": source_url,
                    "token": token,
                    **validation,
                }

    return None


def discover_probe(row):
    for token in company_slug_variants(row["company"]):
        validation = validate_board(token)

        if validation and validation["valid"]:
            return {
                "method": "PROBE",
                "source_url": None,
                "token": token,
                **validation,
            }

        time.sleep(0.08)

    return None


def selected_rows(rows):
    selected = []

    for row in rows:
        notes = (row.get("notes") or "").lower()
        ats_type = (row.get("ats_type") or "").lower()

        if ats_type != "unknown":
            continue

        if "greenhouse" not in notes:
            continue

        if notes.strip().startswith("alias of"):
            continue

        selected.append(row)

    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input",
        nargs="?",
        default="config/employers.csv",
    )
    parser.add_argument(
        "--write",
        default=None,
        help="Write updated staging CSV here.",
    )
    parser.add_argument(
        "--report",
        default="output/greenhouse_discovery.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )
    args = parser.parse_args()

    input_path = Path(args.input)

    with input_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        rows = list(reader)

    targets = selected_rows(rows)
    if args.limit:
        targets = targets[: args.limit]

    print("=" * 72)
    print("GREENHOUSE DISCOVERY")
    print("=" * 72)
    print(f"CSV rows total:          {len(rows)}")
    print(f"Greenhouse rows target:  {len(targets)}")
    print()

    report = []
    found_by_company = {}

    for index, row in enumerate(targets, start=1):
        company = row["company"]

        hit = discover_direct(row)

        if hit is None:
            hit = discover_probe(row)

        if hit:
            found_by_company[company] = hit
            report.append(
                {
                    "company": company,
                    **hit,
                }
            )
            print(
                f"[{index:>3}/{len(targets)}] "
                f"HIT-{hit['method']:<6} "
                f"{company:<34} "
                f"token={hit['token']} jobs={hit['jobs']}"
            )
        else:
            report.append(
                {
                    "company": company,
                    "method": "MISS",
                    "source_url": None,
                    "token": None,
                    "valid": False,
                    "http_status": None,
                    "jobs": None,
                    "sample_title": None,
                    "sample_location": None,
                    "api_url": None,
                }
            )
            print(
                f"[{index:>3}/{len(targets)}] "
                f"miss       {company}"
            )

    direct = sum(
        item["method"] == "DIRECT"
        for item in report
    )
    probe = sum(
        item["method"] == "PROBE"
        for item in report
    )
    misses = sum(
        item["method"] == "MISS"
        for item in report
    )

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Checked:                 {len(targets)}")
    print(f"Direct-source hits:      {direct}")
    print(f"Probe hits:              {probe}")
    print(f"Misses:                  {misses}")
    print()
    print(
        "DIRECT hits may be promoted after review. "
        "PROBE hits require company-identity confirmation."
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    report_path.write_text(
        json.dumps(
            {
                "run_time_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "input_file": str(input_path),
                "checked": len(targets),
                "direct_hits": direct,
                "probe_hits": probe,
                "misses": misses,
                "results": report,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if args.write:
        output_path = Path(args.write)
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        for row in rows:
            hit = found_by_company.get(
                row["company"]
            )
            if not hit:
                continue

            row["ats_type"] = "greenhouse"
            row["ats_identifier"] = hit["token"]
            row["careers_url"] = (
                f"https://job-boards.greenhouse.io/"
                f"{hit['token']}"
            )
            row["enabled"] = "false"

            if hit["method"] == "DIRECT":
                row["notes"] = (
                    "GREENHOUSE-DISCOVERED DIRECT "
                    f"{datetime.now(timezone.utc).date()}; "
                    f"token={hit['token']}; "
                    f"live API jobs={hit['jobs']}; "
                    "kept disabled until production review."
                )
            else:
                row["notes"] = (
                    "GREENHOUSE-PROBE-DISCOVERED "
                    f"{datetime.now(timezone.utc).date()}; "
                    f"token={hit['token']}; "
                    f"live API jobs={hit['jobs']}; "
                    "TECHNICALLY VALID BUT COMPANY IDENTITY "
                    "MUST BE CONFIRMED before enabling."
                )

        with output_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote staging CSV: {output_path}")
        print(f"Wrote report:      {report_path}")


if __name__ == "__main__":
    main()
