import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


EMPLOYERS_FILE = Path("config/employers.csv")
OUTPUT_DIR = Path("output")

SEARCH_TERMS = [
    "java",
    "software engineer",
    "backend",
    "platform",
    "distributed systems",
    "api",
    "cloud",
]

# Workday normally returns at most 20 rows per page.
WORKDAY_PAGE_SIZE = 20

# Safety cap while we build/test the system.
# 5 pages = max 100 rows per keyword per employer.
WORKDAY_MAX_PAGES_PER_TERM = 5


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


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
                    "notes": row.get(
                        "notes", ""
                    ).strip(),
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


# ============================================================
# ORACLE RECRUITING CLOUD
# ============================================================

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

        print(f"      Oracle search: {keyword}")

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
                    "        API failed: "
                    + response.text[:300]
                )
                continue

            try:
                data = response.json()
            except ValueError:
                print(
                    "        ERROR: response was not JSON"
                )
                continue

            parents = data.get("items", [])

            for parent in parents:
                requisitions = (
                    parent.get("requisitionList")
                    or []
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
                        "posted_date_text": None,
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
                        "url": build_oracle_job_url(
                            employer,
                            job_id,
                        ),
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

        except requests.RequestException as exc:
            print(
                f"        Oracle request error: {exc}"
            )

    print(
        f"      Unique Oracle jobs: "
        f"{len(collected)}"
    )

    return list(collected.values())


# ============================================================
# WORKDAY
# ============================================================

def parse_workday_identifier(employer):
    identifier = employer["ats_identifier"]

    if "|" not in identifier:
        return None, None

    tenant, site = identifier.split("|", 1)

    return tenant.strip(), site.strip()


def workday_api_url(employer):
    tenant, site = parse_workday_identifier(
        employer
    )

    if not tenant or not site:
        return None

    parsed = urlparse(employer["careers_url"])

    return (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )


def workday_public_job_url(
    employer,
    site,
    external_path,
):
    parsed = urlparse(employer["careers_url"])

    return (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/en-US/{site}"
        f"{external_path}"
    )


def workday_job_id(external_path):
    if not external_path:
        return ""

    last_segment = external_path.rstrip(
        "/"
    ).split("/")[-1]

    if "_" in last_segment:
        return last_segment.rsplit("_", 1)[-1]

    return last_segment


def normalize_workday_posted_date(
    posted_text,
    run_time,
):
    if not posted_text:
        return None

    text = posted_text.strip().lower()

    if "today" in text:
        return run_time.date().isoformat()

    if "yesterday" in text:
        return (
            run_time.date()
            - timedelta(days=1)
        ).isoformat()

    match = re.search(
        r"(\d+)\s+day",
        text,
    )

    if match:
        days = int(match.group(1))

        return (
            run_time.date()
            - timedelta(days=days)
        ).isoformat()

    return None


def collect_workday(employer, run_time):
    tenant, site = parse_workday_identifier(
        employer
    )

    if not tenant or not site:
        print(
            "      ERROR: Workday ats_identifier "
            "must be tenant|site"
        )
        return []

    api_url = workday_api_url(employer)

    print(f"      Tenant: {tenant}")
    print(f"      Site: {site}")
    print(f"      API: {api_url}")

    collected = {}

    workday_headers = dict(HEADERS)
    workday_headers["Content-Type"] = (
        "application/json"
    )

    for keyword in SEARCH_TERMS:
        print(
            f"      Workday search: {keyword}"
        )

        offset = 0

        for page_number in range(
            1,
            WORKDAY_MAX_PAGES_PER_TERM + 1,
        ):
            payload = {
                "appliedFacets": {},
                "limit": WORKDAY_PAGE_SIZE,
                "offset": offset,
                "searchText": keyword,
            }

            try:
                response = requests.post(
                    api_url,
                    headers=workday_headers,
                    json=payload,
                    timeout=30,
                )

            except requests.RequestException as exc:
                print(
                    f"        Request error: {exc}"
                )
                break

            print(
                f"        Page {page_number} "
                f"HTTP {response.status_code}"
            )

            if response.status_code >= 400:
                print(
                    "        API failed: "
                    + response.text[:300]
                )
                break

            try:
                data = response.json()
            except ValueError:
                print(
                    "        ERROR: response was not JSON"
                )
                break

            postings = (
                data.get("jobPostings")
                or []
            )

            total = data.get("total")

            print(
                f"        Rows: {len(postings)} "
                f"Total: {total}"
            )

            if not postings:
                break

            for posting in postings:
                title = (
                    posting.get("title")
                    or ""
                ).strip()

                external_path = (
                    posting.get(
                        "externalPath"
                    )
                    or ""
                ).strip()

                if not title or not external_path:
                    continue

                if not RELEVANT_TITLE_PATTERN.search(
                    title
                ):
                    continue

                job_id = workday_job_id(
                    external_path
                )

                posted_text = posting.get(
                    "postedOn"
                )

                job = {
                    "company": employer["company"],
                    "title": title,
                    "job_id": job_id,
                    "requisition_number": job_id,
                    "location": posting.get(
                        "locationsText"
                    ),
                    "country": "US",
                    "posted_date": (
                        normalize_workday_posted_date(
                            posted_text,
                            run_time,
                        )
                    ),
                    "posted_date_text": posted_text,
                    "posting_end_date": None,
                    "job_type": None,
                    "job_schedule": None,
                    "job_function": None,
                    "organization": None,
                    "short_description": None,
                    "url": workday_public_job_url(
                        employer,
                        site,
                        external_path,
                    ),
                    "ats_type": "workday",
                    "source": (
                        "official_workday_api"
                    ),
                    "matched_keyword": keyword,
                }

                key = (
                    employer["company"].lower(),
                    job_id.lower()
                    if job_id
                    else external_path.lower(),
                )

                collected[key] = job

            offset += len(postings)

            if (
                isinstance(total, int)
                and offset >= total
            ):
                break

            if len(postings) < WORKDAY_PAGE_SIZE:
                break

        else:
            print(
                "        WARNING: page safety cap "
                f"reached for '{keyword}'"
            )

    print(
        f"      Unique Workday jobs: "
        f"{len(collected)}"
    )

    return list(collected.values())


# ============================================================
# ADAPTER ROUTING
# ============================================================

def collect_jobs(employer, run_time):
    ats_type = employer["ats_type"]

    if ats_type == "oracle_cx":
        return collect_oracle_cx(employer)

    if ats_type == "workday":
        return collect_workday(
            employer,
            run_time,
        )

    return []


# ============================================================
# MAIN
# ============================================================

def main():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    employers = load_employers()

    run_time = datetime.now(
        timezone.utc
    )

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

            if employer["ats_type"] in {
                "oracle_cx",
                "workday",
            }:
                print(
                    f"    Adapter: "
                    f"{employer['ats_type']}"
                )

                jobs = collect_jobs(
                    employer,
                    run_time,
                )

            else:
                print(
                    f"    Adapter not built yet: "
                    f"{employer['ats_type']}"
                )

        result["jobs_found"] = len(jobs)

        for job in jobs:
            key = (
                job["company"].lower(),
                (
                    job.get("job_id")
                    or job["url"]
                ).lower(),
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
        len(coverage_results)
        - reachable
    )

    supported_types = {
        "oracle_cx",
        "workday",
    }

    supported = sum(
        1
        for employer in employers
        if employer["ats_type"]
        in supported_types
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
        "run_time_utc": (
            run_time.isoformat()
        ),
        "employers_total": len(employers),
        "reachable": reachable,
        "failed_or_unverified": failed,
        "employers_with_supported_adapter": (
            supported
        ),
        "candidate_jobs_collected": len(jobs),
        "employers": coverage_results,
    }

    candidates = {
        "run_time_utc": (
            run_time.isoformat()
        ),
        "candidate_count": len(jobs),
        "jobs": jobs,
    }

    with (
        OUTPUT_DIR
        / "latest_coverage.json"
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
        OUTPUT_DIR
        / "latest_candidates.json"
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
        f"Employers configured: "
        f"{len(employers)}"
    )
    print(
        f"Reachable: {reachable}"
    )
    print(
        f"Supported ATS employers: "
        f"{supported}"
    )
    print(
        f"Candidate jobs collected: "
        f"{len(jobs)}"
    )


if __name__ == "__main__":
    main()
