import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


EMPLOYERS_FILE = Path("config/employers.csv")
OUTPUT_DIR = Path("output")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; JobCollector/1.0; "
        "+https://github.com/saiavinashgorla/job-collector)"
    )
}


def load_employers():
    employers = []

    with EMPLOYERS_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            enabled = row.get("enabled", "").strip().lower()

            if enabled not in {"true", "1", "yes", "y"}:
                continue

            employers.append(
                {
                    "company": row.get("company", "").strip(),
                    "careers_url": row.get("careers_url", "").strip(),
                    "ats_type": row.get("ats_type", "").strip() or "unknown",
                    "ats_identifier": row.get("ats_identifier", "").strip(),
                    "notes": row.get("notes", "").strip(),
                }
            )

    return employers


def check_employer(employer):
    company = employer["company"]
    careers_url = employer["careers_url"]

    result = {
        "company": company,
        "careers_url": careers_url,
        "ats_type": employer["ats_type"],
        "status": "not_checked",
        "http_status": None,
        "final_url": None,
        "error": None,
    }

    if not careers_url:
        result["status"] = "missing_url"
        return result

    try:
        response = requests.get(
            careers_url,
            headers=HEADERS,
            timeout=20,
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


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    employers = load_employers()

    run_time = datetime.now(timezone.utc).isoformat()

    coverage_results = []

    for index, employer in enumerate(employers, start=1):
        print(
            f"[{index}/{len(employers)}] Checking "
            f"{employer['company']}..."
        )

        result = check_employer(employer)
        coverage_results.append(result)

        print(
            f"    {result['status']} "
            f"{result['http_status'] or ''}"
        )

    reachable = sum(
        1 for result in coverage_results
        if result["status"] == "reachable"
    )

    failed = len(coverage_results) - reachable

    coverage = {
        "run_time_utc": run_time,
        "employers_total": len(employers),
        "reachable": reachable,
        "failed_or_unverified": failed,
        "employers": coverage_results,
    }

    # We are intentionally not collecting job postings yet.
    # ATS-specific collectors will be added after this base pipeline works.
    candidates = {
        "run_time_utc": run_time,
        "candidate_count": 0,
        "jobs": [],
    }

    with (OUTPUT_DIR / "latest_coverage.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(coverage, file, indent=2)

    with (OUTPUT_DIR / "latest_candidates.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(candidates, file, indent=2)

    print()
    print("Collection complete")
    print(f"Employers checked: {len(employers)}")
    print(f"Reachable: {reachable}")
    print(f"Failed/unverified: {failed}")


if __name__ == "__main__":
    main()
