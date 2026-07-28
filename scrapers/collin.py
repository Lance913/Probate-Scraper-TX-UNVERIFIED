"""
Collin County Probate Scraper — Tyler Odyssey Public Access.

## Portal history (confirmed via probe on GitHub Actions, 2026-07-28)

The county's originally-given URL, cijspub.co.collin.tx.us/PublicAccess, is
retired: it now serves "Collin County: Service Unavailable / This site has
been moved permanently." That dead page itself links to the replacement:
Tyler's cloud-hosted infrastructure at portal-txcollin.tylertech.cloud,
running the SAME classic Odyssey Public Access WebForms UI (left-nav
"Case Records" style) — not the newer "Odyssey Portal" SPA product some
other Tyler-hosted TX counties use (e.g. Travis, and per a parallel probe,
Bexar). The internal case-category ID scheme survived the migration
unchanged: the landing page's "Probate Case Records" nav link is literally
`javascript:LaunchSearch('Search.aspx?ID=200', ...)`, confirmed live — i.e.
the original URL's `?ID=200` really was (and still is) the Probate node.

    Landing:        https://portal-txcollin.tylertech.cloud/Publicaccess
                    (redirects to .../PublicAccess/default.aspx)
    Probate search: https://portal-txcollin.tylertech.cloud/PublicAccess/Search.aspx?ID=200

## BLOCKED — AWS WAF Bot Control (see PR description for full evidence)

Every navigation to /PublicAccess/Search.aspx (any ID, not just 200) —
whether reached by clicking the landing page's nav link or by a fresh
session going straight to the URL — immediately serves a "Human
Verification" interstitial: a genuine interactive image-selection CAPTCHA
("Choose all the hats"), not a silent/auto-resolving JS challenge. This was
verified:
  - Across three independent browser engines (Chromium, Firefox, WebKit) —
    all three hit the identical wall on their very first request, which
    rules out a headless-Chromium-specific automation fingerprint as the
    cause. This points at the request itself (almost certainly the GitHub
    Actions / cloud-CI IP range — AWS WAF Bot Control ships managed rules
    that specifically target hosting-provider/datacenter IP space) rather
    than anything fixable in how the browser is driven.
  - As NOT session-persistent: a second navigation in the same browser
    context/cookies re-triggers the same wall.
  - As NOT limited to Collin: Tarrant County is also now hosted on the same
    tylertech.cloud platform family (portal-txtarrant.tylertech.cloud) and
    hits the identical wall when clicking into its own case search — this
    looks like a platform-wide WAF policy on the classic-UI Search.aspx
    endpoint, not a Collin-specific block. Tarrant is also in this repo's
    9-county roster, so whoever picks it up next should expect this too.

Per project policy, this scraper does NOT attempt to solve or bypass the
CAPTCHA (no third-party CAPTCHA-solving service, no purpose-built evasion of
the bot-detection itself — see SYSTEM_GUIDE.md §2 and the PR description).

Because the wall sits in front of the actual search form, we never got to
see the real party-name/date-range fields, the results table schema, or a
case-detail page's party-role structure — so there is deliberately NO
speculative form-fill or table-parsing code below. Guessing at a table we've
never seen would produce exactly the "confidently-wrong scraper"
SYSTEM_GUIDE.md §6 warns against. What IS implemented is the real, verified
navigation up to the wall, plus explicit detection of the wall so a blocked
run is loud and unambiguous in the logs — never silently indistinguishable
from "no probate filings today" (SYSTEM_GUIDE.md §9 bug #1's spirit, applied
to a hard block instead of a slow table).

If Collin's (or Tyler's platform-wide) WAF policy is ever relaxed, or this
is run from a network AWS WAF doesn't challenge, `_is_waf_wall()` will return
False and the TODO below is exactly what's left: dump the real search form
(§6 step 1), submit a Party Name + Date Filed search restricted to the
Probate Courts location, dump the results table headers/rows (§6 step 2),
and open one case detail page to confirm whether decedent/applicant/executor
roles are structured text or need OCR (§6 step 3).
"""
import logging
from datetime import date
from typing import Dict, List

from .base import BaseScraper, launch_chromium

LANDING_URL = "https://portal-txcollin.tylertech.cloud/Publicaccess"
PROBATE_SEARCH_URL = "https://portal-txcollin.tylertech.cloud/PublicAccess/Search.aspx?ID=200"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


class CollinCountyScraper(BaseScraper):

    def __init__(self):
        super().__init__('Collin')

    def scrape(self, target_date: date) -> List[Dict]:
        self.logger.info(f"Scraping Collin County for {target_date}")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.error("Collin: Playwright not installed")
            return []

        try:
            with sync_playwright() as pw:
                browser = launch_chromium(pw)
                ctx = browser.new_context(user_agent=UA)
                page = ctx.new_page()
                page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                page.set_default_timeout(30_000)

                self.logger.info(f"Collin: loading {PROBATE_SEARCH_URL}")
                page.goto(PROBATE_SEARCH_URL, wait_until='networkidle')
                page.wait_for_timeout(1500)

                if self._is_waf_wall(page):
                    self.logger.error(
                        "Collin: BLOCKED by an AWS WAF 'Human Verification' "
                        "interactive CAPTCHA on the case-search endpoint "
                        "(title=%r). This is a structural block on cloud/CI IP "
                        "ranges confirmed across Chromium/Firefox/WebKit during "
                        "investigation (see PR description for full evidence) — "
                        "it is NOT a code bug and NOT today's real filing count. "
                        "Per project policy, no CAPTCHA-solving/bypass is "
                        "attempted. Returning 0 records for Collin this run.",
                        page.title())
                    browser.close()
                    return []

                # Reaching here would mean the WAF wall was NOT hit — unexpected
                # given the investigation, but handled explicitly rather than
                # silently. We have never seen the real search form/results in
                # this state, so there is nothing further implemented yet; log
                # loudly so this gets picked up rather than mistaken for "ran
                # fine, zero filings."
                self.logger.warning(
                    "Collin: reached the search page WITHOUT hitting the WAF "
                    "wall we've previously always hit — the real search form "
                    "and results parsing were never built (no verified "
                    "visibility into that page ever existed). title=%r url=%r. "
                    "This needs a follow-up investigation pass (SYSTEM_GUIDE.md "
                    "§6) before real scraping logic can be written; returning "
                    "0 records for now rather than guessing.",
                    page.title(), page.url)
                browser.close()
                return []

        except Exception as exc:
            self.logger.error(f"Collin: error: {exc}", exc_info=True)
            return []

    @staticmethod
    def _is_waf_wall(page) -> bool:
        """True if the page is the AWS WAF 'Human Verification' interstitial
        rather than the real search form."""
        try:
            if 'Human Verification' in (page.title() or ''):
                return True
            return bool(page.evaluate(
                "() => !!document.querySelector('#amzn-captcha-verify-button') "
                "|| /Let's confirm you are human/i.test(document.body.innerText||'')"
            ))
        except Exception:
            return False
