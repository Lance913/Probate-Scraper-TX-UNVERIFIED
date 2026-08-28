import requests
import logging
import re
import time
from datetime import date
from typing import List, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Texas probate courts commonly share their docket with non-estate matters
# (guardianships, mental-health commitments, trust-only proceedings). Those
# are not homeowner-estate leads. This is a permissive SAFETY-NET exclude list
# (default to keeping a case unless positively recognized as non-estate) —
# case-type label wording varies by county/platform, so an allow-list would
# silently drop valid estates whose label doesn't match a guessed vocabulary.
# Prefer filtering at the search-form level (case-type checkboxes) first, per
# county, and use this as a second line of defense.
NON_ESTATE_CASE_TYPE_KEYWORDS = [
    'GUARDIANSHIP', 'GUARDIAN OF', 'MENTAL HEALTH', 'MENTAL ILLNESS',
    'COMMITMENT', 'CHEMICALLY DEPENDENT', 'MINOR SETTLEMENT',
    # Found live in a real Denton batch: "Section 1102 Investigations
    # (Court Initiated)" -- a guardianship-adjacent court investigation
    # (Texas Estates Code §1102), not a decedent estate matter, but didn't
    # contain the word GUARDIANSHIP so slipped past the original list.
    '1102 INVESTIGATION', 'INCAPACITATED PERSON',
]

# Reference vocabulary of case types that DO indicate a decedent's estate —
# useful when a portal's advanced search has a case-type filter to select
# (analogous to Collin foreclosure's "Residential Single Family" checkboxes).
# Not exhaustive; verify actual labels against the real portal before relying
# on this, per the investigation methodology in SYSTEM_GUIDE.md §6.
ESTATE_CASE_TYPE_HINTS = [
    'ADMINISTRATION', 'INDEPENDENT ADMINISTRATION', 'DEPENDENT ADMINISTRATION',
    'MUNIMENT OF TITLE', 'SMALL ESTATE', 'HEIRSHIP', 'DETERMINATION OF HEIRSHIP',
    'PROBATE OF WILL', 'LETTERS TESTAMENTARY', 'LETTERS OF ADMINISTRATION',
    'ESTATE OF', 'DECEDENT',
]


def is_estate_case(case_type: str) -> bool:
    """True unless the case type is positively recognized as NOT a decedent's
    estate matter (guardianship, mental health, etc). Unknown/blank -> keep."""
    if not case_type:
        return True
    up = case_type.upper()
    return not any(k in up for k in NON_ESTATE_CASE_TYPE_KEYWORDS)


def launch_chromium(pw, **kwargs):
    """Launch headless Chromium, self-healing if the browser binary is missing.

    On GitHub Actions a stale Playwright cache can leave the Chromium build absent
    ("Executable doesn't exist"), which would silently lose an entire county. If
    that happens we install the browser at runtime and retry once."""
    import subprocess
    import sys
    args = kwargs.pop('args', ['--disable-blink-features=AutomationControlled'])
    try:
        return pw.chromium.launch(headless=True, args=args, **kwargs)
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or 'playwright install' in msg:
            logging.getLogger('base').warning("Chromium missing — installing at runtime…")
            subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
                           check=False)
            return pw.chromium.launch(headless=True, args=args, **kwargs)
        raise


class BaseScraper:
    """Shared utilities for all county probate scrapers."""

    def __init__(self, county_name: str):
        self.county = county_name
        self.logger = logging.getLogger(county_name)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scrape(self, target_date: date) -> List[Dict]:
        raise NotImplementedError("Each county scraper must implement scrape()")

    # ── Name helpers ─────────────────────────────────────────────────────────

    def parse_name(self, full_name: str):
        """
        Split 'JOHN A DOE' → ('John', 'A Doe'). Handles probate-index name
        formats too, e.g. 'DOE, JOHN A' (last-comma-first) — pass
        comma_is_last_first=True for that form via parse_name_lf() instead.
        Strips 'ET AL', 'ET UX', '& JANE', etc. Returns (first_name, last_name).
        """
        full_name = full_name.strip().upper()
        full_name = re.sub(
            r'\s+(ET\s+AL|ET\s+UX|AND\s+|&\s+).*$', '', full_name, flags=re.I
        ).strip()
        full_name = full_name.strip(',.;')
        parts = full_name.split()
        if len(parts) >= 2:
            return parts[0].title(), ' '.join(parts[1:]).title()
        elif len(parts) == 1:
            return '', parts[0].title()
        return '', full_name.title()

    def parse_name_lf(self, raw: str):
        """Split a 'LAST, FIRST MIDDLE' style name (common in court case
        indices/dockets) into (first_name, last_name)."""
        raw = raw.strip().strip(',.;')
        if ',' in raw:
            last, _, first = raw.partition(',')
            return first.strip().title(), last.strip().title()
        return self.parse_name(raw)

    # ── Address helpers ───────────────────────────────────────────────────────

    def parse_address(self, raw: str):
        """
        Try to pull street / city / zip from a raw address string.
        Returns (address, city, zip_code).
        """
        raw = raw.strip()
        m = re.search(
            r'^([\d]+[^,]+),\s*([A-Za-z\s]+),\s*TX\s*(\d{5})',
            raw, re.I
        )
        if m:
            return m.group(1).strip(), m.group(2).strip().title(), m.group(3)
        return raw, '', ''

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(3):
            try:
                r = self.session.get(url, timeout=30, **kwargs)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                self.logger.warning(f"GET {url} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return None

    def post(self, url: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(3):
            try:
                r = self.session.post(url, timeout=30, **kwargs)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                self.logger.warning(f"POST {url} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return None

    def build_record(self, **kwargs) -> Dict:
        """Normalise field names into the canonical probate output dict."""
        return {
            'decedent_first_name': kwargs.get('decedent_first_name', ''),
            'decedent_last_name':  kwargs.get('decedent_last_name', ''),
            'address':             kwargs.get('address', ''),
            'city':                kwargs.get('city', ''),
            'state':               kwargs.get('state', 'TX'),
            'zip_code':            kwargs.get('zip_code', ''),
            'county':              self.county,
            'case_number':         kwargs.get('case_number', ''),
            'case_type':           kwargs.get('case_type', ''),
            'filing_date':         kwargs.get('filing_date', ''),
            'executor_name':       kwargs.get('executor_name', ''),
            'executor_address':    kwargs.get('executor_address', ''),
        }
