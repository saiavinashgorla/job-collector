import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


EMPLOYERS_FILE = Path("config/employers.csv")
OUTPUT_DIR = Path("output")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

SEARCH_TERMS = [
    "java",
    "software engineer",
    "backend",
    "platform",
    "distributed systems",
    "api",
    "cloud",
]

RELEVANT_TITLE_PATTERN = re.compile(
    r"\b("
    r"software|engineer|developer|backend|back-end|java|jvm|"
    r"platform|distributed|cloud|api|application"
    r")\b",
    re.IGNORECASE,
)


def load_employers():
    employers = []

    with EMPLOYERS_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            enabled = row.get("enabled", "").strip().lower()

            if enabled not in {"true", "1", "yes", "y"}:
                continue

            employers.append(
                {
                    "company": row.get("company", "").strip(),
                    "careers_url": row.get(
                        "careers_url", ""
                    ).strip(),
                    "ats_type": row.get(
                        "ats_type", ""
                    ).strip() or "unknown",
                    "ats_identifier": row.get(
                        "ats_identifier", ""
                    ).strip(),
                    "notes": row.get("notes", "").strip(),
                }
            )

    return employers


def check_employer(employer):
    result = {
        "company": employer["company"],
        "careers_url": employer["careers_url"],
        "ats_type": employer["ats_type"],
        "status": "not_checked",
        "http_status": None,
        "final_url": None,
        "error": None,
        "jobs_found": 0,
    }

    if not employer["careers_url"]:
        result["status"] = "missing_url"
        return result

    try:
        response = requests.get(
            employer["careers_url"],
            headers=HEADERS,
            timeout=25,
            allow_redirects=True,
        )

        result["http_status"] = response.status_code
        result["final_url"] = response.url

        if response.status_code < 400:
            result["status"] = "reachable"
        else:
            result["status"] = "http_error"

    except requests.RequestException as exc:
        result["status"] = "request_error"
        result["error"] = str(exc)

    return result


def oracle_api_url(employer):
    parsed = urlparse(employer["careers_url"])

    return (
        f"{parsed.scheme}://{parsed.netloc}"
        "/hcmRestApi/resources/latest/"
        "recruitingCEJobRequisitions"
    )


def build_oracle_job_url(employer, job_id):
    return (
        employer["careers_url"].rstrip("/")
        + f"/job/{job_id}"
    )


def collect_oracle_cx(employer):
    site_number = employer["ats_identifier"]

    if not site_number:
        print("      ERROR: missing Oracle site number")
        return []

    api_url = oracle_api_url(employer)

    collected = {}

    for keyword in SEARCH_TERMS:
        finder = (
            "findReqs;"
            f"siteNumber={site_number},"
            f"keyword={keyword},"
            "limit=50,"
            "offset=0,"
            "sortBy=POSTING_DATES_DESC"
        )

        params = {
            "onlyData": "true",
            "expand": "requisitionList",
            "finder": finder,
        }

        print(f"      Searching API: {keyword}")

        try:
            response = requests.get(
                api_url,
                headers=HEADERS,
                params=params,
                timeout=30,
            )

            print(
                f"        HTTP status: "
                f"{response.status_code}"
            )

            if response.status_code >= 400:
                print(
                    "        API request failed: "
                    + response.text[:300]
                )
                continue

            try:
                data = response.json()
            except ValueError:
                print(
                    "        ERROR: response was not JSON"
                )
                print(response.text[:300])
                continue

            parents = data.get("items", [])

            print(
                f"        API parent records: "
                f"{len(parents)}"
            )

            jobs_this_search = 0

            for parent in parents:
                total_jobs = parent.get(
                    "TotalJobsCount"
                )

                if total_jobs is not None:
                    print(
                        f"        TotalJobsCount: "
                        f"{total_jobs}"
                    )

                requisitions = parent.get(
                    "requisitionList"
                ) or []

                print(
                    f"        Requisition rows: "
                    f"{len(requisitions)}"
                )

                for req in requisitions:
                    title = (
                        req.get("Title") or ""
                    ).strip()

                    if not title:
                        continue

                    if not RELEVANT_TITLE_PATTERN.search(
                        title
                    ):
                        continue

                    country = (
                        req.get(
                            "PrimaryLocationCountry"
                        )
                        or ""
                    ).strip()

                    # Our search is focused on US roles.
                    # Keep rows with no country because some
                    # Oracle boards omit this field.
                    if (
                        country
                        and country.upper()
                        not in {"US", "USA"}
                    ):
                        continue

                    job_id = str(
                        req.get("Id")
                        or req.get("RequisitionId")
                        or ""
                    ).strip()

                    if not job_id:
                        continue

                    job_url = build_oracle_job_url(
                        employer,
                        job_id,
                    )

                    job = {
                        "company": employer["company"],
                        "title": title,
                        "job_id": job_id,
                        "requisition_number": (
                            req.get(
                                "RequisitionNumber"
                            )
                        ),
                        "location": req.get(
                            "PrimaryLocation"
                        ),
                        "country": country or None,
                        "posted_date": req.get(
                            "PostedDate"
                        ),
                        "posting_end_date": req.get(
                            "PostingEndDate"
                        ),
                        "job_type": req.get(
                            "JobType"
                        ),
                        "job_schedule": req.get(
                            "JobSchedule"
                        ),
                        "job_function": req.get(
                            "JobFunction"
                        ),
                        "organization": req.get(
                            "Organization"
                        ),
                        "short_description": req.get(
                            "ShortDescriptionStr"
                        ),
                        "url": job_url,
                        "ats_type": "oracle_cx",
                        "source": (
                            "official_oracle_recruiting_api"
                        ),
                        "matched_keyword": keyword,
                    }

                    key = (
                        employer["company"].lower(),
                        job_id.lower(),
                    )

                    collected[key] = job
                    jobs_this_search += 1

            print(
                f"        Matching jobs this search: "
                f"{jobs_this_search}"
            )

        except requests.RequestException as exc:
            print(
                f"        API request error: {exc}"
            )

    print(
        f"      Unique Oracle jobs collected: "
        f"{len(collected)}"
    )

    return list(collected.values())


def collect_jobs(employer):
    if employer["ats_type"] == "oracle_cx":
        return collect_oracle_cx(employer)

    return []


def main():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    employers = load_employers()

    run_time = datetime.now(
        timezone.utc
    ).isoformat()

    coverage_results = []
    all_jobs = {}

    for index, employer in enumerate(
        employers,
        start=1,
    ):
        print()
        print("=" * 60)
        print(
            f"[{index}/{len(employers)}] "
            f"{employer['company']}"
        )
        print("=" * 60)

        result = check_employer(employer)

        print(
            f"    Site status: "
            f"{result['status']} "
            f"{result['http_status'] or ''}"
        )

        jobs = []

        if result["status"] == "reachable":

            if employer["ats_type"] == "oracle_cx":
                print(
                    "    Adapter: Oracle Recruiting Cloud"
                )

                jobs = collect_jobs(employer)

            else:
                print(
                    f"    Adapter not built yet: "
                    f"{employer['ats_type']}"
                )

        result["jobs_found"] = len(jobs)

        for job in jobs:
            key = (
                job["company"].lower(),
                job["job_id"].lower(),
            )

            all_jobs[key] = job

        coverage_results.append(result)

        print(
            f"    Jobs collected: {len(jobs)}"
        )

    reachable = sum(
        1
        for result in coverage_results
        if result["status"] == "reachable"
    )

    failed = (
        len(coverage_results) - reachable
    )

    supported = sum(
        1
        for employer in employers
        if employer["ats_type"] == "oracle_cx"
    )

    jobs = list(all_jobs.values())

    jobs.sort(
        key=lambda item: (
            item.get("posted_date") or "",
            item["company"].lower(),
            item["title"].lower(),
        ),
        reverse=True,
    )

    coverage = {
        "run_time_utc": run_time,
        "employers_total": len(employers),
        "reachable": reachable,
        "failed_or_unverified": failed,
        "employers_with_supported_adapter": supported,
        "candidate_jobs_collected": len(jobs),
        "employers": coverage_results,
    }

    candidates = {
        "run_time_utc": run_time,
        "candidate_count": len(jobs),
        "jobs": jobs,
    }

    with (
        OUTPUT_DIR / "latest_coverage.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            coverage,
            file,
            indent=2,
            ensure_ascii=False,
        )

    with (
        OUTPUT_DIR / "latest_candidates.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            candidates,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("=" * 60)
    print("COLLECTION COMPLETE")
    print("=" * 60)
    print(
        f"Employers configured: {len(employers)}"
    )
    print(f"Reachable: {reachable}")
    print(
        f"Supported ATS employers: {supported}"
    )
    print(
        f"Candidate jobs collected: {len(jobs)}"
    )


if __name__ == "__main__":
    main()
