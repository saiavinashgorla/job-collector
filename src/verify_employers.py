#!/usr/bin/env python3
"""
Live verifier for config/employers.csv.

What "correct" means here:
- Workday: the mapped tenant/site responds through the public CXS /jobs endpoint
  with the expected Workday JSON structure.
- Oracle Recruiting Cloud: the mapped host/site identifier responds through the
  recruitingCEJobRequisitions API with the expected Oracle JSON structure.

The verifier DOES NOT mutate employers.csv. It writes:
- output/employer_verification.json
- output/employer_verification.csv

Default scope: enabled rows that already have a Workday or Oracle mapping.
Use --scope candidates to also test disabled low-confidence mappings while
skipping rows explicitly marked as aliases.
"""

import argparse
import csv
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


SUPPORTED_ATS = {"workday", "oracle_cx"}
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
THREAD_LOCAL = threading.local()

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def truthy(value):
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def get_session():
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        THREAD_LOCAL.session = session
    return session


def request_with_retries(method, url, *, timeout=25, max_attempts=3, **kwargs):
    session = get_session()
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = session.request(
                method,
                url,
                timeout=timeout,
                allow_redirects=True,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == max_attempts:
                return None, last_error
            time.sleep((0.8 * attempt) + random.uniform(0.0, 0.3))
            continue

        if response.status_code not in RETRYABLE or attempt == max_attempts:
            return response, None

        time.sleep((0.8 * attempt) + random.uniform(0.0, 0.3))

    return None, last_error or "request failed"


def parse_workday_identifier(value):
    value = (value or "").strip()
    if "|" not in value:
        return None, None
    tenant, site = value.split("|", 1)
    tenant = tenant.strip()
    site = site.strip()
    return tenant or None, site or None


def validate_workday(row):
    company = row["company"]
    careers_url = row["careers_url"].strip()
    tenant, site = parse_workday_identifier(row["ats_identifier"])

    result = base_result(row)

    if not careers_url or not tenant or not site:
        result.update(
            status="FAIL",
            reason="Missing careers_url or Workday tenant|site identifier",
        )
        return result

    parsed = urlparse(careers_url)
    if not parsed.scheme or not parsed.netloc:
        result.update(status="FAIL", reason="Invalid careers_url")
        return result

    api_url = (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )

    headers = dict(DEFAULT_HEADERS)
    headers["Content-Type"] = "application/json"
    headers["Referer"] = careers_url

    payload = {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": "",
    }

    response, request_error = request_with_retries(
        "POST",
        api_url,
        headers=headers,
        json=payload,
    )

    result["api_url"] = api_url

    if request_error:
        result.update(status="ERROR", reason=request_error)
        return result

    result["http_status"] = response.status_code

    if response.status_code in {401, 403, 429}:
        result.update(
            status="BLOCKED",
            reason=f"HTTP {response.status_code}; mapping not disproven",
        )
        return result

    if response.status_code >= 400:
        result.update(
            status="FAIL",
            reason=f"HTTP {response.status_code}",
        )
        return result

    try:
        data = response.json()
    except ValueError:
        result.update(
            status="FAIL",
            reason="HTTP 200 but response was not JSON",
        )
        return result

    postings = data.get("jobPostings")
    total = data.get("total")

    if not isinstance(postings, list):
        result.update(
            status="FAIL",
            reason="JSON did not contain Workday jobPostings list",
        )
        return result

    if total is None:
        total = len(postings)

    result["jobs_available"] = total

    if postings:
        sample = postings[0]
        result["sample_title"] = sample.get("title")
        result["sample_location"] = sample.get("locationsText")
        result.update(
            status="PASS",
            reason="Valid Workday CXS job inventory",
        )
    else:
        result.update(
            status="PASS_EMPTY",
            reason="Valid Workday CXS endpoint, currently returned 0 jobs",
        )

    return result


def validate_oracle(row):
    careers_url = row["careers_url"].strip()
    site_number = row["ats_identifier"].strip()

    result = base_result(row)

    if not careers_url or not site_number:
        result.update(
            status="FAIL",
            reason="Missing careers_url or Oracle site identifier",
        )
        return result

    parsed = urlparse(careers_url)
    if not parsed.scheme or not parsed.netloc:
        result.update(status="FAIL", reason="Invalid careers_url")
        return result

    api_url = (
        f"{parsed.scheme}://{parsed.netloc}"
        "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    )

    finder = (
        "findReqs;"
        f"siteNumber={site_number},"
        "keyword=software engineer,"
        "limit=5,"
        "offset=0,"
        "sortBy=POSTING_DATES_DESC"
    )

    params = {
        "onlyData": "true",
        "expand": "requisitionList",
        "finder": finder,
    }

    response, request_error = request_with_retries(
        "GET",
        api_url,
        params=params,
        headers=DEFAULT_HEADERS,
    )

    result["api_url"] = api_url

    if request_error:
        result.update(status="ERROR", reason=request_error)
        return result

    result["http_status"] = response.status_code

    if response.status_code in {401, 403, 429}:
        result.update(
            status="BLOCKED",
            reason=f"HTTP {response.status_code}; mapping not disproven",
        )
        return result

    if response.status_code >= 400:
        result.update(
            status="FAIL",
            reason=f"HTTP {response.status_code}",
        )
        return result

    try:
        data = response.json()
    except ValueError:
        result.update(
            status="FAIL",
            reason="HTTP 200 but response was not JSON",
        )
        return result

    items = data.get("items")
    if not isinstance(items, list):
        result.update(
            status="FAIL",
            reason="JSON did not contain Oracle items list",
        )
        return result

    requisitions = []
    for item in items:
        if isinstance(item, dict):
            reqs = item.get("requisitionList") or []
            if isinstance(reqs, list):
                requisitions.extend(reqs)

    result["jobs_available"] = len(requisitions)

    if requisitions:
        sample = requisitions[0]
        result["sample_title"] = sample.get("Title")
        result["sample_location"] = sample.get("PrimaryLocation")
        result.update(
            status="PASS",
            reason="Valid Oracle Recruiting Cloud inventory",
        )
    else:
        # A structurally valid Oracle response is evidence that the host/site
        # mapping is usable even if this search keyword currently finds no jobs.
        result.update(
            status="PASS_EMPTY",
            reason=(
                "Valid Oracle Recruiting Cloud response; "
                "current verification query returned 0 jobs"
            ),
        )

    return result


def base_result(row):
    return {
        "company": row.get("company", "").strip(),
        "ats_type": row.get("ats_type", "").strip(),
        "ats_identifier": row.get("ats_identifier", "").strip(),
        "careers_url": row.get("careers_url", "").strip(),
        "enabled": truthy(row.get("enabled")),
        "notes": row.get("notes", "").strip(),
        "status": None,
        "http_status": None,
        "jobs_available": None,
        "sample_title": None,
        "sample_location": None,
        "api_url": None,
        "reason": None,
    }


def should_verify(row, scope):
    ats_type = (row.get("ats_type") or "").strip()
    if ats_type not in SUPPORTED_ATS:
        return False

    if not (row.get("careers_url") or "").strip():
        return False

    if not (row.get("ats_identifier") or "").strip():
        return False

    if scope == "enabled":
        return truthy(row.get("enabled"))

    # candidates = enabled mappings + disabled guesses, but skip known aliases.
    notes = (row.get("notes") or "").lower()
    if "alias of" in notes:
        return False

    return True


def endpoint_key(row):
    ats_type = row["ats_type"].strip()
    careers_url = row["careers_url"].strip()
    parsed = urlparse(careers_url)
    host = parsed.netloc.lower()

    if ats_type == "workday":
        tenant, site = parse_workday_identifier(row["ats_identifier"])
        return ("workday", host, (tenant or "").lower(), (site or "").lower())

    return (
        "oracle_cx",
        host,
        row["ats_identifier"].strip().lower(),
    )


def validate_row(row):
    ats_type = row["ats_type"].strip()
    if ats_type == "workday":
        return validate_workday(row)
    if ats_type == "oracle_cx":
        return validate_oracle(row)

    result = base_result(row)
    result.update(status="SKIP", reason="Unsupported ATS type")
    return result


def write_csv(path, results):
    fields = [
        "company",
        "ats_type",
        "ats_identifier",
        "enabled",
        "status",
        "http_status",
        "jobs_available",
        "sample_title",
        "sample_location",
        "careers_url",
        "api_url",
        "reason",
        "notes",
    ]

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field) for field in fields})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="config/employers.csv",
        help="Path to employers.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for verification reports",
    )
    parser.add_argument(
        "--scope",
        choices=("enabled", "candidates"),
        default="enabled",
        help=(
            "enabled = verify active Workday/Oracle mappings only; "
            "candidates = also test disabled non-alias mapping guesses"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Concurrent verifier workers (default: 6)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        all_rows = list(csv.DictReader(file))

    rows = [row for row in all_rows if should_verify(row, args.scope)]

    # Do not hammer the same ATS endpoint twice.
    unique_rows = []
    duplicates = []
    seen = {}

    for row in rows:
        key = endpoint_key(row)
        if key in seen:
            duplicates.append(
                {
                    "company": row["company"],
                    "duplicate_of": seen[key],
                    "ats_type": row["ats_type"],
                    "ats_identifier": row["ats_identifier"],
                }
            )
            continue

        seen[key] = row["company"]
        unique_rows.append(row)

    print("=" * 72)
    print("EMPLOYER ATS VERIFICATION")
    print("=" * 72)
    print(f"CSV rows total:             {len(all_rows)}")
    print(f"Rows in requested scope:    {len(rows)}")
    print(f"Unique ATS endpoints:       {len(unique_rows)}")
    print(f"Duplicate endpoints skipped:{len(duplicates)}")
    print(f"Workers:                    {args.workers}")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_to_company = {
            executor.submit(validate_row, row): row["company"]
            for row in unique_rows
        }

        completed = 0
        total = len(future_to_company)

        for future in as_completed(future_to_company):
            company = future_to_company[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "company": company,
                    "status": "ERROR",
                    "reason": f"Unhandled verifier error: {exc}",
                }

            results.append(result)
            completed += 1

            status = result.get("status", "ERROR")
            jobs = result.get("jobs_available")
            jobs_text = "" if jobs is None else f" jobs={jobs}"

            print(
                f"[{completed:>3}/{total}] "
                f"{status:<10} {company}{jobs_text}"
            )

    status_order = {
        "PASS": 0,
        "PASS_EMPTY": 1,
        "BLOCKED": 2,
        "FAIL": 3,
        "ERROR": 4,
        "SKIP": 5,
    }

    results.sort(
        key=lambda item: (
            status_order.get(item.get("status"), 99),
            (item.get("company") or "").lower(),
        )
    )

    counts = {}
    for result in results:
        status = result.get("status") or "UNKNOWN"
        counts[status] = counts.get(status, 0) + 1

    correct = counts.get("PASS", 0) + counts.get("PASS_EMPTY", 0)
    tested = len(results)
    conclusive = (
        correct
        + counts.get("FAIL", 0)
    )

    summary = {
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": str(input_path),
        "scope": args.scope,
        "csv_rows_total": len(all_rows),
        "rows_selected": len(rows),
        "unique_endpoints_tested": tested,
        "duplicate_endpoints_skipped": len(duplicates),
        "correct_mappings": correct,
        "pass_with_jobs": counts.get("PASS", 0),
        "pass_empty_but_valid": counts.get("PASS_EMPTY", 0),
        "failed_mappings": counts.get("FAIL", 0),
        "blocked_or_rate_limited": counts.get("BLOCKED", 0),
        "network_or_runtime_errors": counts.get("ERROR", 0),
        "conclusive_tests": conclusive,
        "counts_by_status": counts,
    }

    json_path = output_dir / "employer_verification.json"
    csv_path = output_dir / "employer_verification.csv"

    with json_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "summary": summary,
                "results": results,
                "duplicate_endpoints": duplicates,
            },
            file,
            indent=2,
            ensure_ascii=False,
        )

    write_csv(csv_path, results)

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Correct mappings:           {correct}")
    print(f"  PASS with jobs:           {counts.get('PASS', 0)}")
    print(f"  PASS_EMPTY but valid:     {counts.get('PASS_EMPTY', 0)}")
    print(f"Failed mappings:            {counts.get('FAIL', 0)}")
    print(f"Blocked/rate-limited:       {counts.get('BLOCKED', 0)}")
    print(f"Network/runtime errors:     {counts.get('ERROR', 0)}")
    print()
    print(f"JSON report: {json_path}")
    print(f"CSV report:  {csv_path}")

    # Important: do not fail the GitHub Action just because mappings are wrong.
    # We want the reports committed so they can be reviewed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
