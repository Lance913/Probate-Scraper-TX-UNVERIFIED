"""
Thin per-county wrapper classes for counties that share a PORTAL scraper
class (SYSTEM_GUIDE.md Sec 3/Sec 5 -- "one scraper class per platform, not
per county"), mirroring the sister foreclosure repo's scrapers/counties.py.

Each wrapper is a one-liner: slug + display name + base URL. All actual
scraping logic lives in the shared platform module. Every county here has
still been individually probed (see that module's docstring / PR
description) -- sharing a platform class is never assumed from URL pattern
alone.
"""
from .tyler_odyssey import TylerOdysseyScraper


class TarrantCountyScraper(TylerOdysseyScraper):
    """Classic Tyler Odyssey Public Access, now Tyler-cloud-hosted.

    CONFIRMED BLOCKED by an AWS WAF Bot Control interactive CAPTCHA on every
    Search.aspx entry point reachable from GitHub Actions' datacenter IP
    range (see scrapers/tyler_odyssey.py module docstring). The shared
    class's WAF detection logs this loudly and returns [] rather than
    silently reporting "0 filings" -- left wired in (not skipped) so it
    starts working automatically the moment the block is lifted (e.g. a
    human decision to scrape from a non-datacenter egress IP), with no code
    change required.
    """
    def __init__(self):
        super().__init__('tarrant', 'Tarrant', 'https://odyssey.tarrantcounty.com/PublicAccess')


class DentonCountyScraper(TylerOdysseyScraper):
    """Classic Tyler Odyssey Public Access, self-hosted on dentoncounty.gov
    (NOT tylertech.cloud) -- confirmed WAF-free. Two numbered probate courts
    (no combined "all probate" option), handled automatically by the shared
    class's per-location-option loop."""
    def __init__(self):
        super().__init__('denton', 'Denton', 'https://justice1.dentoncounty.gov/PublicAccess')


# Johnson County: NOT wired in yet. The reference-spreadsheet link
# (johnson.tx.publicsearch.us) is confirmed WRONG for probate -- that's the
# County Clerk's document-RECORDING platform ("Official Record Search...
# County Clerk"), almost certainly a copy-paste of the foreclosure link, not
# a case-search system. The real candidate, https://pa.johnsoncountytx.org/
# DistrictClerkPA/Login.aspx (Tyler Odyssey Public Access branding, and
# Google's own index titles that exact URL "Odyssey Public Access"), and its
# hypothesized CountyClerkPA sibling, both connection-reset from 3 separate
# GitHub Actions runs (different paths, different attempts) -- see PR
# description. A raw `requests`-level cross-check (bypassing the browser
# fingerprint entirely, to tell a host-level block from a Playwright-
# specific one) was queued but never returned a result before an unrelated
# account-wide GitHub Actions billing block hit (see PR description). Add a
# JohnsonCountyScraper wrapper here once that's resolved.
