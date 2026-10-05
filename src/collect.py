import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup


EMPLOYERS_FILE = Path("config/employers.csv")
OUTPUT_DIR = Path("output")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    )
}

SEARCH_TERMS = [
    "software engineer",
    "backend",
    "java",
    "platform",
    "distributed systems",
    "cloud engineer",
    "api",
]

RELEVANT_TITLE_PATTERN = re.compile(
    r"\b("
    r"software|backend|back-end|java|jvm|platform|"
    r"distributed|cloud|api|application|developer"
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


def oracle_search_url(employer, keyword):
    base = employer["careers_url"].rstrip("/")

    params = {
        "keyword": keyword,
        "sortBy": "POSTING_DATES_DESC",
    }

    return f"{base}/jobs?{urlencode(params)}"


def extract_oracle_job_links(html, page_url):
    soup = BeautifulSoup(html, "html.parser")

    jobs = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()

        if "/job/" not in href:
            continue

        title = " ".join(
            anchor.get_text(" ", strip=True).split()
        )

        if not title:
            continue

        if not RELEVANT_TITLE_PATTERN.search(title):
            continue

        full_url = urljoin(page_url, href)

        # Remove tracking/query parameters for deduplication.
        clean_url = full_url.split("?")[0].rstrip("/")

        jobs[clean_url] = {
            "title": title,
            "url": clean_url,
        }

    return list(jobs.values())


def collect_oracle_cx(employer):
    collected = {}

    for keyword in SEARCH_TERMS:
        search_url = oracle_search_url(
            employer,
            keyword,
        )

        print(f"      Search: {keyword}")

        try:
            response = requests.get(
                search_url,
                headers=HEADERS,
                timeout=30,
                allow_redirects=True,
            )

            if response.status_code >= 400:
                print(
                    f"        HTTP {response.status_code}"
                )
                continue

            jobs = extract_oracle_job_links(
                response.text,
                response.url,
            )

            print(
                f"        Relevant links found: {len(jobs)}"
            )

            for job in jobs:
                job["company"] = employer["company"]
                job["ats_type"] = "oracle_cx"
                job["source"] = "official_career_site"

                collected[job["url"]] = job

        except requests.RequestException as exc:
            print(f"        Search failed: {exc}")

    return list(collected.values())


def collect_jobs(employer):
    ats_type = employer["ats_type"]

    if ats_type == "oracle_cx":
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
        print(
            f"[{index}/{len(employers)}] "
            f"{employer['company']}"
        )

        result = check_employer(employer)

        print(
            f"    Site status: "
            f"{result['status']} "
            f"{result['http_status'] or ''}"
        )

        jobs = []

        if result["status"] == "reachable":
            if employer["ats_type"] == "oracle_cx":
                print("    Running Oracle CX collector")
                jobs = collect_jobs(employer)

            else:
                print(
                    f"    No adapter yet for "
                    f"{employer['ats_type']}"
                )

        result["jobs_found"] = len(jobs)

        for job in jobs:
            key = (
                job["company"].lower(),
                job["url"].lower(),
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
            item["company"].lower(),
            item["title"].lower(),
        )
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
    print("==========================")
    print("COLLECTION COMPLETE")
    print("==========================")
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
