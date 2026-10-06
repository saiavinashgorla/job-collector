import csv
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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

WORKDAY_PAGE_SIZE = 20

# Workday's public CXS endpoint is happiest at 20 rows/page.
# Ten pages gives us a 200-row safety window per keyword.
WORKDAY_MAX_PAGES_PER_TERM = 10

GREENHOUSE_API_ROOT = "https://boards-api.greenhouse.io/v1/boards"

REQUEST_TIMEOUT_SECONDS = 30
DETAIL_RETRY_STATUSES = {429, 500, 502, 503, 504}

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

US_COUNTRY_NAMES = {
    "US",
    "USA",
    "U.S.",
    "U.S.A.",
    "UNITED STATES",
    "UNITED STATES OF AMERICA",
}

# Used only as a fallback for string-only additional locations.
# IN and OR are intentionally omitted because they are common English tokens
# and can create false positives when country metadata is missing.
US_STATE_ABBREVIATIONS_SAFE = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IA", "KS", "KY", "LA", "ME", "MD", "MA",
    "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
    "NY", "NC", "ND", "OH", "OK", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

US_STATE_NAMES = {
    "ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA",
    "COLORADO", "CONNECTICUT", "DELAWARE", "FLORIDA", "GEORGIA",
    "HAWAII", "IDAHO", "ILLINOIS", "INDIANA", "IOWA", "KANSAS",
    "KENTUCKY", "LOUISIANA", "MAINE", "MARYLAND", "MASSACHUSETTS",
    "MICHIGAN", "MINNESOTA", "MISSISSIPPI", "MISSOURI", "MONTANA",
    "NEBRASKA", "NEVADA", "NEW HAMPSHIRE", "NEW JERSEY",
    "NEW MEXICO", "NEW YORK", "NORTH CAROLINA", "NORTH DAKOTA",
    "OHIO", "OKLAHOMA", "OREGON", "PENNSYLVANIA", "RHODE ISLAND",
    "SOUTH CAROLINA", "SOUTH DAKOTA", "TENNESSEE", "TEXAS", "UTAH",
    "VERMONT", "VIRGINIA", "WASHINGTON", "WEST VIRGINIA",
    "WISCONSIN", "WYOMING", "DISTRICT OF COLUMBIA",
}


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

    if employer["ats_type"] == "greenhouse":
        board_token = greenhouse_board_token(employer)

        if not board_token:
            result["status"] = "missing_identifier"
            result["error"] = (
                "Greenhouse requires ats_identifier=board_token "
                "or a recognizable Greenhouse board URL"
            )
            return result

        api_url = greenhouse_api_url_from_token(board_token)

        try:
            response = requests.get(
                api_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            result["http_status"] = response.status_code
            result["final_url"] = response.url
            result["status"] = (
                "reachable"
                if response.status_code < 400
                else "http_error"
            )
        except requests.RequestException as exc:
            result["status"] = "request_error"
            result["error"] = str(exc)

        return result

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
                timeout=REQUEST_TIMEOUT_SECONDS,
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


def workday_api_base(employer):
    tenant, site = parse_workday_identifier(
        employer
    )

    if not tenant or not site:
        return None

    parsed = urlparse(employer["careers_url"])

    return (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/wday/cxs/{tenant}/{site}"
    )


def workday_api_url(employer):
    base = workday_api_base(employer)

    if not base:
        return None

    return f"{base}/jobs"


def workday_detail_url(
    employer,
    external_path,
):
    base = workday_api_base(employer)

    if not base or not external_path:
        return None

    # externalPath starts with /job/...
    return f"{base}{external_path}"


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


def country_descriptor_is_us(value):
    if not value:
        return False

    return value.strip().upper() in US_COUNTRY_NAMES


def looks_explicitly_us_location(text):
    """
    Conservative fallback used only when Workday did not provide
    structured country metadata for an additional location.
    False negatives are preferable to admitting a non-US job.
    """
    if not text:
        return False

    original = str(text).strip()
    upper = original.upper()

    if any(
        name in upper
        for name in (
            "UNITED STATES OF AMERICA",
            "UNITED STATES",
            " U.S.A.",
            " U.S. ",
            " USA",
        )
    ):
        return True

    for state_name in US_STATE_NAMES:
        if re.search(
            rf"\b{re.escape(state_name)}\b",
            upper,
        ):
            return True

    # Common Workday US formats:
    # "TX - Work from home", "Austin, TX", "TX - Irving"
    match = re.search(
        r"(?:^|,\s*|\s)"
        r"([A-Z]{2})"
        r"(?:\s+-|,|$)",
        original,
    )

    if (
        match
        and match.group(1)
        in US_STATE_ABBREVIATIONS_SAFE
    ):
        return True

    return False


def normalized_location_text(value):
    if isinstance(value, str):
        return value.strip() or None

    if isinstance(value, dict):
        return (
            value.get("descriptor")
            or value.get("location")
            or value.get("name")
        )

    return None


def workday_detail_is_us(job_info):
    """
    Returns (is_us, detected_us_locations).

    Primary decision uses Workday's structured ISO alpha-2 country code.
    If the primary location is outside the US, we also inspect additional
    locations so a multi-country posting that genuinely includes a US
    location can still be kept.
    """
    if not isinstance(job_info, dict):
        return False, []

    us_locations = []

    requisition_location = (
        job_info.get("jobRequisitionLocation")
        or {}
    )

    primary_country = (
        requisition_location.get("country")
        or {}
    )

    primary_code = (
        primary_country.get("alpha2Code")
        or ""
    ).strip().upper()

    primary_descriptor = (
        primary_country.get("descriptor")
        or ""
    ).strip()

    top_country = (
        job_info.get("country")
        or {}
    )

    top_country_descriptor = ""

    if isinstance(top_country, dict):
        top_country_descriptor = (
            top_country.get("descriptor")
            or ""
        ).strip()

    primary_location = (
        job_info.get("location")
        or requisition_location.get("descriptor")
        or ""
    )

    primary_is_us = (
        primary_code == "US"
        or country_descriptor_is_us(
            primary_descriptor
        )
        or country_descriptor_is_us(
            top_country_descriptor
        )
    )

    if primary_is_us:
        if primary_location:
            us_locations.append(
                str(primary_location).strip()
            )
        return True, us_locations

    additional_locations = (
        job_info.get("additionalLocations")
        or []
    )

    for item in additional_locations:
        if isinstance(item, dict):
            item_country = (
                item.get("country")
                or {}
            )

            item_code = (
                item_country.get("alpha2Code")
                or ""
            ).strip().upper()

            item_descriptor = (
                item_country.get("descriptor")
                or ""
            ).strip()

            location_text = (
                normalized_location_text(item)
            )

            if (
                item_code == "US"
                or country_descriptor_is_us(
                    item_descriptor
                )
                or looks_explicitly_us_location(
                    location_text
                )
            ):
                if location_text:
                    us_locations.append(
                        location_text
                    )

        elif isinstance(item, str):
            if looks_explicitly_us_location(
                item
            ):
                us_locations.append(
                    item.strip()
                )

    if us_locations:
        return True, us_locations

    # Last-resort fallback only when Workday omitted structured country data.
    if (
        not primary_code
        and not primary_descriptor
        and not top_country_descriptor
        and looks_explicitly_us_location(
            primary_location
        )
    ):
        if primary_location:
            us_locations.append(
                str(primary_location).strip()
            )
        return True, us_locations

    return False, []


def fetch_workday_detail(
    employer,
    external_path,
):
    detail_url = workday_detail_url(
        employer,
        external_path,
    )

    if not detail_url:
        return None

    detail_headers = dict(HEADERS)
    detail_headers["Accept"] = "application/json"
    detail_headers["Referer"] = (
        employer["careers_url"]
    )

    for attempt in range(1, 4):
        try:
            response = requests.get(
                detail_url,
                headers=detail_headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

        except requests.RequestException as exc:
            if attempt == 3:
                print(
                    "        Detail request error: "
                    f"{exc}"
                )
                return None

            time.sleep(0.5 * attempt)
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                print(
                    "        Detail response was not JSON"
                )
                return None

            info = data.get(
                "jobPostingInfo"
            )

            if isinstance(info, dict):
                return info

            print(
                "        Detail JSON missing "
                "jobPostingInfo"
            )
            return None

        if (
            response.status_code
            in DETAIL_RETRY_STATUSES
            and attempt < 3
        ):
            time.sleep(0.5 * attempt)
            continue

        print(
            "        Detail HTTP "
            f"{response.status_code}: "
            f"{response.text[:200]}"
        )
        return None

    return None


def workday_display_location(
    job_info,
    posting,
    us_locations,
):
    requisition_location = (
        job_info.get("jobRequisitionLocation")
        or {}
    )

    primary_country = (
        requisition_location.get("country")
        or {}
    )

    primary_code = (
        primary_country.get("alpha2Code")
        or ""
    ).strip().upper()

    primary_location = (
        job_info.get("location")
        or requisition_location.get("descriptor")
    )

    if primary_code == "US" and primary_location:
        return primary_location

    if us_locations:
        return us_locations[0]

    if primary_location:
        return primary_location

    return posting.get("locationsText")


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
    detail_cache = {}

    skipped_non_us = 0
    skipped_unverified = 0

    workday_headers = dict(HEADERS)
    workday_headers["Content-Type"] = (
        "application/json"
    )
    workday_headers["Referer"] = (
        employer["careers_url"]
    )

    for keyword in SEARCH_TERMS:
        print(
            f"      Workday search: {keyword}"
        )

        offset = 0
        expected_total = None

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
                    timeout=REQUEST_TIMEOUT_SECONDS,
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

            if page_number == 1:
                raw_total = data.get("total")
                if isinstance(raw_total, int):
                    expected_total = raw_total

            print(
                f"        Rows: {len(postings)} "
                f"Total(first page): "
                f"{expected_total}"
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

                if external_path in detail_cache:
                    job_info = detail_cache[
                        external_path
                    ]
                else:
                    job_info = fetch_workday_detail(
                        employer,
                        external_path,
                    )
                    detail_cache[
                        external_path
                    ] = job_info

                if not job_info:
                    skipped_unverified += 1
                    continue

                can_apply = job_info.get(
                    "canApply"
                )

                if can_apply is False:
                    continue

                is_us, us_locations = (
                    workday_detail_is_us(
                        job_info
                    )
                )

                if not is_us:
                    skipped_non_us += 1
                    continue

                job_id = str(
                    job_info.get("jobReqId")
                    or workday_job_id(
                        external_path
                    )
                    or ""
                ).strip()

                if not job_id:
                    continue

                posted_text = (
                    job_info.get("postedOn")
                    or posting.get("postedOn")
                )

                posted_date = (
                    job_info.get("startDate")
                    or normalize_workday_posted_date(
                        posted_text,
                        run_time,
                    )
                )

                job_url = (
                    job_info.get("externalUrl")
                    or workday_public_job_url(
                        employer,
                        site,
                        external_path,
                    )
                )

                location = (
                    workday_display_location(
                        job_info,
                        posting,
                        us_locations,
                    )
                )

                job = {
                    "company": employer["company"],
                    "title": (
                        job_info.get("title")
                        or title
                    ),
                    "job_id": job_id,
                    "requisition_number": job_id,
                    "location": location,
                    "locations_summary": posting.get(
                        "locationsText"
                    ),
                    "country": "US",
                    "country_verified": True,
                    "posted_date": posted_date,
                    "posted_date_text": posted_text,
                    "posting_end_date": None,
                    "job_type": job_info.get(
                        "timeType"
                    ),
                    "job_schedule": None,
                    "job_function": None,
                    "organization": None,
                    "remote_type": job_info.get(
                        "remoteType"
                    ),
                    "can_apply": can_apply,
                    "short_description": None,
                    "url": job_url,
                    "ats_type": "workday",
                    "source": (
                        "official_workday_api"
                    ),
                    "matched_keyword": keyword,
                }

                key = (
                    employer["company"].lower(),
                    job_id.lower(),
                )

                collected[key] = job

            offset += len(postings)

            if (
                expected_total is not None
                and offset >= expected_total
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
        f"      Unique Workday US jobs: "
        f"{len(collected)}"
    )
    print(
        f"      Non-US rows skipped: "
        f"{skipped_non_us}"
    )
    print(
        f"      Unverified detail rows skipped: "
        f"{skipped_unverified}"
    )

    return list(collected.values())


# ============================================================
# GREENHOUSE JOB BOARD
# ============================================================

def greenhouse_board_token(employer):
    """
    Return the public Greenhouse board token.

    Preferred config:
        ats_type=greenhouse
        ats_identifier=<board token>
    """
    identifier = (
        employer.get("ats_identifier")
        or ""
    ).strip()

    if identifier:
        if "://" not in identifier:
            return identifier.strip("/")

        candidate_url = identifier
    else:
        candidate_url = (
            employer.get("careers_url")
            or ""
        ).strip()

    if not candidate_url:
        return None

    try:
        parsed = urlparse(candidate_url)
    except ValueError:
        return None

    host = parsed.netloc.lower()
    path_parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    if host in {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
    }:
        if path_parts:
            return path_parts[0]

        query = parse_qs(parsed.query)
        board_for = query.get("for")
        if board_for:
            return board_for[0]

    if host == "boards-api.greenhouse.io":
        try:
            boards_index = path_parts.index("boards")
            return path_parts[boards_index + 1]
        except (ValueError, IndexError):
            return None

    return None


def greenhouse_api_url_from_token(board_token):
    return (
        f"{GREENHOUSE_API_ROOT}/"
        f"{board_token}/jobs"
    )


def greenhouse_api_url(employer):
    board_token = greenhouse_board_token(employer)

    if not board_token:
        return None

    return greenhouse_api_url_from_token(board_token)


def strip_html_text(value):
    if not value:
        return None

    text = re.sub(
        r"(?is)<(script|style).*?>.*?</\1>",
        " ",
        str(value),
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = re.sub(r"\s+", " ", text).strip()

    return text or None


def greenhouse_location_strings(job):
    values = []

    location = job.get("location")
    if isinstance(location, dict):
        name = location.get("name")
        if name:
            values.append(str(name))

    offices = job.get("offices") or []
    for office in offices:
        if not isinstance(office, dict):
            continue

        for key in ("name", "location"):
            value = office.get(key)
            if value:
                values.append(str(value))

    metadata = job.get("metadata") or []
    for item in metadata:
        if not isinstance(item, dict):
            continue

        name = str(
            item.get("name") or ""
        ).lower()

        if not any(
            marker in name
            for marker in (
                "location",
                "country",
                "state",
                "region",
                "remote",
            )
        ):
            continue

        value = item.get("value")

        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    values.append(entry)
                elif isinstance(entry, dict):
                    for key in ("name", "value", "label"):
                        nested = entry.get(key)
                        if nested:
                            values.append(str(nested))
        elif isinstance(value, dict):
            for key in ("name", "value", "label"):
                nested = value.get(key)
                if nested:
                    values.append(str(nested))

    return list(
        dict.fromkeys(
            value.strip()
            for value in values
            if value and value.strip()
        )
    )


def greenhouse_location_is_us(text):
    if not text:
        return False

    value = str(text).strip()
    upper = value.upper()

    if looks_explicitly_us_location(value):
        return True

    if upper in US_COUNTRY_NAMES:
        return True

    if re.search(
        r"(?:^|[\s,(/-])"
        r"(?:US|USA|U\.S\.|U\.S\.A\.)"
        r"(?:$|[\s,)/-])",
        upper,
    ):
        return True

    return False


def greenhouse_job_is_us(job):
    location_values = greenhouse_location_strings(job)

    us_locations = [
        value
        for value in location_values
        if greenhouse_location_is_us(value)
    ]

    return bool(us_locations), us_locations


def greenhouse_updated_date(updated_at):
    if not updated_at:
        return None

    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})",
        str(updated_at).strip(),
    )

    return match.group(1) if match else None


def collect_greenhouse(employer, run_time):
    board_token = greenhouse_board_token(employer)

    if not board_token:
        print(
            "      ERROR: Greenhouse ats_identifier "
            "must contain the public board token"
        )
        return []

    api_url = greenhouse_api_url_from_token(board_token)

    print(f"      Board token: {board_token}")
    print(f"      API: {api_url}")

    try:
        response = requests.get(
            api_url,
            headers=HEADERS,
            params={"content": "true"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"      Greenhouse request error: {exc}")
        return []

    print(f"      Greenhouse HTTP: {response.status_code}")

    if response.status_code >= 400:
        print("      API failed: " + response.text[:300])
        return []

    try:
        data = response.json()
    except ValueError:
        print("      ERROR: Greenhouse response was not JSON")
        return []

    postings = data.get("jobs") or []

    if not isinstance(postings, list):
        print("      ERROR: Greenhouse JSON missing jobs list")
        return []

    collected = {}
    skipped_non_us = 0

    for posting in postings:
        if not isinstance(posting, dict):
            continue

        title = (posting.get("title") or "").strip()

        if not title:
            continue

        if not RELEVANT_TITLE_PATTERN.search(title):
            continue

        is_us, us_locations = greenhouse_job_is_us(posting)

        if not is_us:
            skipped_non_us += 1
            continue

        job_id = str(posting.get("id") or "").strip()
        job_url = (posting.get("absolute_url") or "").strip()

        if not job_id or not job_url:
            continue

        raw_location = posting.get("location") or {}
        if isinstance(raw_location, dict):
            raw_location = raw_location.get("name") or ""

        location = (
            us_locations[0]
            if us_locations
            else str(raw_location).strip()
        )

        updated_at = posting.get("updated_at")
        description = strip_html_text(posting.get("content"))

        departments = [
            item.get("name")
            for item in (posting.get("departments") or [])
            if isinstance(item, dict) and item.get("name")
        ]

        offices = [
            item.get("name")
            for item in (posting.get("offices") or [])
            if isinstance(item, dict) and item.get("name")
        ]

        job = {
            "company": employer["company"],
            "title": title,
            "job_id": job_id,
            "requisition_number": job_id,
            "location": location or None,
            "locations_summary": (
                " | ".join(us_locations)
                if us_locations
                else location or None
            ),
            "country": "US",
            "country_verified": True,
            # Public Greenhouse exposes updated_at, not guaranteed original posted date.
            "posted_date": greenhouse_updated_date(updated_at),
            "posted_date_text": (
                f"Greenhouse updated_at: {updated_at}"
                if updated_at
                else None
            ),
            "posting_end_date": None,
            "job_type": None,
            "job_schedule": None,
            "job_function": (
                " | ".join(departments)
                if departments
                else None
            ),
            "organization": (
                " | ".join(offices)
                if offices
                else None
            ),
            "remote_type": (
                "remote"
                if any(
                    "REMOTE" in value.upper()
                    for value in us_locations
                )
                else None
            ),
            "can_apply": True,
            "short_description": (
                description[:2000]
                if description
                else None
            ),
            "url": job_url,
            "ats_type": "greenhouse",
            "source": "official_greenhouse_job_board_api",
            "matched_keyword": "title_filter",
            "greenhouse_updated_at": updated_at,
        }

        key = (
            employer["company"].lower(),
            job_id.lower(),
        )
        collected[key] = job

    print(
        f"      Unique Greenhouse US jobs: "
        f"{len(collected)}"
    )
    print(
        f"      Non-US title matches skipped: "
        f"{skipped_non_us}"
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

    if ats_type == "greenhouse":
        return collect_greenhouse(
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
                "greenhouse",
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
        "greenhouse",
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