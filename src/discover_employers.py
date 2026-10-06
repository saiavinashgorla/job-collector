#!/usr/bin/env python3
"""
discover_employers.py

Safely discover ATS mappings for:
  1) currently-unmapped employer rows, and
  2) rows that FAILED the latest employer_verification.json run.

Important:
- Discovery NEVER enables a row.
- Direct links found from the employer's own careers page are higher confidence.
- Workday CXS probe hits are marked PROBE-DISCOVERED and require company-identity
  confirmation even if verify_employers.py later proves the endpoint is technically valid.
- BLOCKED verifier rows are NOT replaced unless --include-blocked is supplied.
- Writes to a staging CSV; do not overwrite config/employers.csv on the first run.

Examples:
  python src/discover_employers.py --self-test

  python src/discover_employers.py config/employers.csv \
    --verification output/employer_verification.json \
    --include-failed --limit 20

  python src/discover_employers.py config/employers.csv \
    --verification output/employer_verification.json \
    --include-failed \
    --probe-misses \
    --write output/employers_discovered.csv
"""

import argparse
import csv
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests


TODAY = date.today().isoformat()

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; employer-discovery/2.0)",
    "Accept-Language": "en-US,en;q=0.9",
}

# Ordered by how often we have seen these in the current employer universe.
WD_HOSTS = ["wd1", "wd5", "wd3", "wd12", "wd501", "wd503", "wd103", "wd108"]

COMMON_SITES = [
    "External",
    "Careers",
    "careers",
    "EXT",
    "Ext",
    "jobs",
    "Jobs",
    "Search",
    "External_Careers",
    "External_Career_Site",
    "ExternalCareers",
    "External_Career",
    "Global",
    "External_Site",
    "SearchJobs",
]

OTHER_ATS = [
    (r"(?:boards|job-boards)\.greenhouse\.io|greenhouse\.io", "Greenhouse"),
    (r"lever\.co", "Lever"),
    (r"ashbyhq\.com", "Ashby"),
    (r"smartrecruiters\.com", "SmartRecruiters"),
    (r"taleo\.net", "Oracle Taleo"),
    (r"successfactors\.(?:com|eu)|jobs\.sap\.com|rmk\.", "SAP SuccessFactors"),
    (r"icims\.com", "iCIMS"),
    (r"jobvite\.com", "Jobvite"),
    (r"eightfold\.ai", "Eightfold"),
    (r"avature\.net", "Avature"),
    (r"brassring\.com|kenexa", "BrassRing"),
    (r"ultipro\.com|ukg\.com/careers", "UKG"),
    (r"bamboohr\.com", "BambooHR"),
    (r"rippling\.com", "Rippling"),
    (r"workable\.com", "Workable"),
    (r"paylocity\.com", "Paylocity"),
    (r"dayforcehcm\.com", "Dayforce"),
    (r"adp\.com", "ADP"),
]

# These are commonly branded career-site layers in front of the real ATS.
# Seeing one is a clue, not proof of the underlying ATS, so discovery must continue.
FRONTENDS = [
    (r"phenompeople|phenom\.com", "Phenom"),
    (r"radancy\.com|tmp\.com", "Radancy"),
]

WD_RE = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
    re.I,
)

ORC_RE = re.compile(
    r"https?://([a-z0-9.-]+\.oraclecloud\.com)/"
    r"hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/"
    r"sites/([A-Za-z0-9_-]+)",
    re.I,
)

SKIP_SITES = {
    "login",
    "job",
    "jobs",
    "apply",
    "userhome",
    "introduceyourself",
    "page",
    "en-us",
}

THREAD_LOCAL = threading.local()


def session():
    s = getattr(THREAD_LOCAL, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(UA)
        THREAD_LOCAL.session = s
    return s


def get(url, timeout=15):
    try:
        return session().get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None


def post(url, *, json_body, timeout=15):
    try:
        return session().post(
            url,
            headers={**UA, "Accept": "application/json"},
            json=json_body,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None


def slugs_for(company):
    first_name = company.split("/")[0]
    base = re.sub(r"[^a-z0-9 ]", "", first_name.lower())
    stop = {
        "inc",
        "corp",
        "corporation",
        "company",
        "co",
        "the",
        "group",
        "holdings",
        "international",
        "global",
        "technologies",
        "technology",
        "systems",
        "financial",
    }
    words = [w for w in base.split() if w not in stop]
    candidates = []

    if words:
        candidates.append("".join(words))
        candidates.append(words[0])
    if len(words) > 1:
        candidates.append("".join(w[0] for w in words))

    return [
        s
        for s in dict.fromkeys(candidates)
        if 2 < len(s) < 30
    ]


def scan_text(text):
    """
    Return:
      ("workday", tenant, wd_host, site)
      ("oracle_cx", host, site)
      ("other", ats_name)
      None
    """
    if not text:
        return None

    for m in WD_RE.finditer(text):
        tenant, wd, site = m.group(1), m.group(2), m.group(3)
        if site.lower() not in SKIP_SITES and not site.lower().startswith("wday"):
            return ("workday", tenant.lower(), wd.lower(), site)

    for m in ORC_RE.finditer(text):
        return ("oracle_cx", m.group(1).lower(), m.group(2))

    for pattern, name in OTHER_ATS:
        if re.search(pattern, text, re.I):
            return ("other", name)

    for pattern, name in FRONTENDS:
        if re.search(pattern, text, re.I):
            return ("frontend", name)

    return None


def guessed_career_urls(company):
    urls = []
    for slug in slugs_for(company)[:2]:
        urls.extend(
            [
                f"https://www.{slug}.com/careers",
                f"https://careers.{slug}.com",
                f"https://jobs.{slug}.com",
            ]
        )
    return urls


def scrape(company, careers_url, pause=0.15):
    urls = []
    if careers_url:
        urls.append(careers_url)
    urls.extend(guessed_career_urls(company))

    frontend_hint = None

    for url in dict.fromkeys(urls):
        r = get(url)
        time.sleep(pause)

        if r is None or r.status_code >= 400:
            continue

        # r.url is important because vanity URLs often redirect to the real ATS.
        text = r.url + "\n" + r.text[:400_000]
        hit = scan_text(text)
        if not hit:
            continue

        # Phenom/Radancy can sit in front of Workday, Oracle, etc.
        # Record the clue but keep looking for the underlying ATS.
        if hit[0] == "frontend":
            frontend_hint = (hit, r.url)
            continue

        return hit, r.url

    if frontend_hint:
        return frontend_hint

    return None, None


def current_workday_hint(row):
    """
    If a row already had a failed Workday guess, try that tenant/host first while
    testing alternate site names. This is much cheaper than guessing everything.
    """
    if (row.get("ats_type") or "").strip() != "workday":
        return None

    identifier = (row.get("ats_identifier") or "").strip()
    careers_url = (row.get("careers_url") or "").strip()

    if "|" not in identifier or not careers_url:
        return None

    tenant, _site = identifier.split("|", 1)
    host = urlparse(careers_url).netloc.lower()

    m = re.match(
        rf"^{re.escape(tenant.lower())}\.(wd\d+)\.myworkdayjobs\.com$",
        host,
        re.I,
    )
    if not m:
        return None

    return tenant.lower(), m.group(1).lower()


def workday_probe_pairs(company, row):
    pairs = []

    hinted = current_workday_hint(row)
    if hinted:
        pairs.append(hinted)

    slugs = slugs_for(company)[:2]
    for tenant in slugs:
        for wd in WD_HOSTS:
            pairs.append((tenant, wd))

    return list(dict.fromkeys(pairs))


def probe_workday(row, *, pause=0.12, max_requests=64):
    """
    Probe Workday directly.

    Deliberately DOES NOT use a bare-host 404 existence check. A real Workday
    tenant can return 404 at / even while its CXS endpoint is valid.

    A technical CXS hit proves only that an endpoint exists. It does NOT prove
    that the tenant belongs to the intended employer.
    """
    company = row["company"]
    payload = {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": "",
    }

    attempts = 0

    for tenant, wd in workday_probe_pairs(company, row):
        base = f"https://{tenant}.{wd}.myworkdayjobs.com"

        for site in COMMON_SITES:
            if attempts >= max_requests:
                return None

            attempts += 1
            url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
            r = post(url, json_body=payload)
            time.sleep(pause)

            if r is None or r.status_code != 200:
                continue

            try:
                data = r.json()
            except ValueError:
                continue

            postings = data.get("jobPostings")
            if isinstance(postings, list):
                return {
                    "tenant": tenant,
                    "wd": wd,
                    "site": site,
                    "total": data.get("total", len(postings)),
                    "sample_title": postings[0].get("title") if postings else None,
                    "requests_used": attempts,
                }

    return None


def load_verification(path):
    if not path:
        return {}

    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    statuses = {}
    for result in data.get("results", []):
        company = (result.get("company") or "").strip()
        if company:
            statuses[company] = result.get("status")

    return statuses


def is_alias(row):
    return (row.get("notes") or "").strip().lower().startswith("alias of")


def is_known_other_ats(row):
    notes = (row.get("notes") or "").lower()
    return (
        "not workday/oracle cx:" in notes
        and "mapping pending" not in notes
    )


def should_process(row, statuses, args):
    company = (row.get("company") or "").strip()
    ats_type = (row.get("ats_type") or "").strip()
    notes = (row.get("notes") or "").strip()

    if not company or is_alias(row):
        return False

    status = statuses.get(company)

    if args.include_failed and status == "FAIL":
        return True

    if args.include_blocked and status == "BLOCKED":
        return True

    # Existing unknown rows are discovery candidates unless we already identified
    # a non-Workday/Oracle ATS.
    if ats_type == "unknown" and not is_known_other_ats(row):
        if args.only_pending:
            return "mapping pending" in notes.lower()
        return True

    return False


def apply_direct_hit(row, hit, source_url):
    old = {
        "ats_type": row.get("ats_type", ""),
        "ats_identifier": row.get("ats_identifier", ""),
        "careers_url": row.get("careers_url", ""),
    }

    if hit[0] == "workday":
        _, tenant, wd, site = hit
        row.update(
            careers_url=f"https://{tenant}.{wd}.myworkdayjobs.com/{site}",
            ats_type="workday",
            ats_identifier=f"{tenant}|{site}",
            enabled="false",
            notes=(
                f"DISCOVERED {TODAY} from employer careers/redirect source {source_url}; "
                f"unverified. Previous mapping: {old['ats_type']} "
                f"{old['ats_identifier']} {old['careers_url']}".strip()
            ),
        )
        return f"WORKDAY {tenant}|{site} ({wd})"

    if hit[0] == "oracle_cx":
        _, host, site = hit
        row.update(
            careers_url=(
                f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}"
            ),
            ats_type="oracle_cx",
            ats_identifier=site,
            enabled="false",
            notes=(
                f"DISCOVERED {TODAY} from employer careers/redirect source {source_url}; "
                f"unverified. Previous mapping: {old['ats_type']} "
                f"{old['ats_identifier']} {old['careers_url']}".strip()
            ),
        )
        return f"ORACLE {host} {site}"

    if hit[0] == "other":
        ats_name = hit[1]
        row.update(
            ats_type="unknown",
            ats_identifier="",
            enabled="false",
            notes=(
                f"Not Workday/Oracle CX: {ats_name} "
                f"(discovered {TODAY} from {source_url}). "
                f"Previous mapping: {old['ats_type']} "
                f"{old['ats_identifier']} {old['careers_url']}".strip()
            ),
        )
        return f"other: {ats_name}"

    return None


def apply_probe_hit(row, probe):
    old = {
        "ats_type": row.get("ats_type", ""),
        "ats_identifier": row.get("ats_identifier", ""),
        "careers_url": row.get("careers_url", ""),
    }

    tenant = probe["tenant"]
    wd = probe["wd"]
    site = probe["site"]

    row.update(
        careers_url=f"https://{tenant}.{wd}.myworkdayjobs.com/{site}",
        ats_type="workday",
        ats_identifier=f"{tenant}|{site}",
        enabled="false",
        notes=(
            f"PROBE-DISCOVERED {TODAY} by Workday CXS "
            f"({probe['total']} jobs; sample={probe['sample_title']!r}). "
            f"TECHNICALLY UNVERIFIED FOR COMPANY IDENTITY: confirm this tenant belongs "
            f"to {row['company']} before enabling, even if verify_employers.py passes. "
            f"Previous mapping: {old['ats_type']} "
            f"{old['ats_identifier']} {old['careers_url']}".strip()
        ),
    )

    return (
        f"WORKDAY(probe) {tenant}|{site} "
        f"({wd}, {probe['total']} jobs, {probe['requests_used']} req)"
    )


def discover_one(row, args):
    hit, source = scrape(
        row["company"],
        (row.get("careers_url") or "").strip(),
        pause=args.pause,
    )

    frontend_hint = None

    if hit and hit[0] != "frontend":
        tag = apply_direct_hit(row, hit, source)
        return row["company"], tag, "direct"

    if hit and hit[0] == "frontend":
        frontend_hint = f"{hit[1]} front end at {source}"

    if args.probe_misses:
        probe = probe_workday(
            row,
            pause=args.pause,
            max_requests=args.probe_max_requests,
        )
        if probe:
            tag = apply_probe_hit(row, probe)
            if frontend_hint:
                row["notes"] = (
                    row["notes"] + f" Front-end clue: {frontend_hint}."
                )
            return row["company"], tag, "probe"

    # Preserve an existing failed mapping rather than silently erasing it.
    suffix = (
        f"Discovery {TODAY}: no alternate ATS portal found."
        if not frontend_hint
        else f"Discovery {TODAY}: saw {frontend_hint}, but underlying ATS remains unresolved."
    )
    row["notes"] = f"{row.get('notes', '').strip()} {suffix}".strip()

    return row["company"], (
        f"frontend-only: {frontend_hint}" if frontend_hint else None
    ), "miss"


def offline_parser_tests():
    cases = [
        (
            "https://coke.wd1.myworkdayjobs.com/coca-cola-careers/job/X",
            ("workday", "coke", "wd1", "coca-cola-careers"),
        ),
        (
            "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123",
            ("oracle_cx", "efds.fa.em5.oraclecloud.com", "CX_1"),
        ),
        (
            "https://job-boards.greenhouse.io/example",
            ("other", "Greenhouse"),
        ),
        (
            "https://example.phenompeople.com/us/en",
            ("frontend", "Phenom"),
        ),
    ]

    ok = True
    for text, expected in cases:
        got = scan_text(text)
        passed = got == expected
        ok &= passed
        print(
            f"{'PASS' if passed else 'FAIL'} parser "
            f"expected={expected} got={got}"
        )

    return ok


def network_self_test():
    """
    Test known direct ATS URLs plus the same Fiserv CXS POST shape used by probing.
    These tests avoid relying on a branded careers page exposing the ATS in raw HTML.
    """
    ok = offline_parser_tests()

    direct_cases = [
        (
            "Target",
            "https://target.wd5.myworkdayjobs.com/targetcareers",
            ("workday", "target", "wd5", "targetcareers"),
        ),
        (
            "Ford",
            "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
            ("oracle_cx", "efds.fa.em5.oraclecloud.com", "CX_1"),
        ),
    ]

    for company, url, expected in direct_cases:
        r = get(url)
        got = None
        if r is not None and r.status_code < 400:
            got = scan_text(r.url + "\n" + r.text[:250_000])

        passed = got == expected
        ok &= passed
        print(
            f"{'PASS' if passed else 'FAIL'} network {company:<8} "
            f"expected={expected} got={got} "
            f"http={getattr(r, 'status_code', None)}"
        )

    fiserv_url = (
        "https://fiserv.wd5.myworkdayjobs.com/"
        "wday/cxs/fiserv/EXT/jobs"
    )
    r = post(
        fiserv_url,
        json_body={
            "appliedFacets": {},
            "limit": 5,
            "offset": 0,
            "searchText": "",
        },
    )

    passed = False
    total = None
    if r is not None and r.status_code == 200:
        try:
            data = r.json()
            postings = data.get("jobPostings")
            passed = isinstance(postings, list)
            total = data.get("total")
        except ValueError:
            passed = False

    ok &= passed
    print(
        f"{'PASS' if passed else 'FAIL'} network Fiserv CXS "
        f"http={getattr(r, 'status_code', None)} total={total}"
    )

    print()
    print("SELF-TEST", "PASSED" if ok else "FAILED")
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "csv_path",
        nargs="?",
        help="Input employers.csv",
    )
    parser.add_argument(
        "--verification",
        help="Latest employer_verification.json; enables FAIL/BLOCKED targeting",
    )
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Also rediscover rows whose latest verification status is FAIL",
    )
    parser.add_argument(
        "--include-blocked",
        action="store_true",
        help="Also rediscover BLOCKED rows (off by default)",
    )
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="For unknown rows, require notes to contain 'mapping pending'",
    )
    parser.add_argument(
        "--probe-misses",
        action="store_true",
        help="After scraping, try bounded Workday CXS probes",
    )
    parser.add_argument(
        "--probe-max-requests",
        type=int,
        default=64,
        help="Maximum Workday CXS POST attempts per company (default: 64)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most N selected rows",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Concurrent companies to process (default: 6)",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.12,
        help="Pause after each HTTP request inside a worker",
    )
    parser.add_argument(
        "--write",
        help="Write the staged CSV here; omit for a dry run",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run parser + live-network smoke tests and exit",
    )

    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(0 if network_self_test() else 1)

    if not args.csv_path:
        parser.error("csv_path is required unless --self-test is used")

    statuses = load_verification(args.verification)

    with Path(args.csv_path).open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames

    selected = [
        row
        for row in rows
        if should_process(row, statuses, args)
    ]

    if args.limit is not None:
        selected = selected[: args.limit]

    print("=" * 72)
    print("ATS DISCOVERY")
    print("=" * 72)
    print(f"CSV rows total:          {len(rows)}")
    print(f"Rows selected:           {len(selected)}")
    print(f"Include FAIL rows:       {args.include_failed}")
    print(f"Include BLOCKED rows:    {args.include_blocked}")
    print(f"Probe misses:            {args.probe_misses}")
    print(f"Probe request cap/row:   {args.probe_max_requests}")
    print(f"Workers:                 {args.workers}")
    print()

    found_direct = 0
    found_probe = 0
    misses = 0

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(discover_one, row, args): row["company"]
            for row in selected
        }

        completed = 0
        total = len(futures)

        for future in as_completed(futures):
            company = futures[future]
            completed += 1

            try:
                _company, tag, kind = future.result()
            except Exception as exc:
                tag = None
                kind = "error"
                print(
                    f"[{completed:>3}/{total}] ERROR {company:<35} {exc}"
                )
                continue

            if kind == "direct":
                found_direct += 1
                prefix = "HIT-direct"
            elif kind == "probe":
                found_probe += 1
                prefix = "HIT-probe "
            else:
                misses += 1
                prefix = "miss      "

            print(
                f"[{completed:>3}/{total}] {prefix} "
                f"{company:<35} {tag or ''}"
            )

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Checked:                 {len(selected)}")
    print(f"Direct-source hits:      {found_direct}")
    print(f"Probe hits:              {found_probe}")
    print(f"Misses:                  {misses}")
    print()
    print(
        "Reminder: probe hits require company-identity confirmation; "
        "a verifier PASS alone is not enough."
    )

    if args.write:
        out = Path(args.write)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fields,
                lineterminator="\r\n",
            )
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote staging CSV: {out}")


if __name__ == "__main__":
    main()
