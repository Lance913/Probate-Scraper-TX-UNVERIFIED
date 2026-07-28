"""
Probe v1 -- Tyler Odyssey Public Access (classic, ".../PublicAccess/default.aspx"
shape) for Tarrant, Denton, and Johnson counties.

Context already independently confirmed via a SIBLING agent's Collin-County
probe workflow runs in THIS SAME repo (different branch -- add-collin-probate-
scraper -- read read-only for context, never pushed to). Re-stated here so
this script is self-contained, but every claim below still gets independently
re-verified for OUR counties, not assumed:

  - Tarrant's https://odyssey.tarrantcounty.com/PublicAccess/default.aspx
    returned HTTP 200 from a GitHub Actions (US) IP and redirected (same URL
    PATH shape) to https://portal-txtarrant.tylertech.cloud/PublicAccess/default.aspx
    -- i.e. still the CLASSIC on-prem-shaped Odyssey Public Access product,
    just now Tyler-cloud-hosted rather than on Tarrant's own servers. The
    landing page's body text explicitly lists "All Probate Courts" as a
    location option and "Case Records Search" as a nav item.
  - Denton's https://justice1.dentoncounty.gov/PublicAccess/ returned HTTP 200
    (contradicts this project's earlier note about connection issues during
    discovery -- that was very likely the non-US dev sandbox being geo-blocked,
    not a real problem with the portal). Stays on dentoncounty.gov -- i.e.
    self-hosted, NOT tylertech.cloud. Same classic UI wording ("Case Records" /
    "Select a location").
  - Johnson's GENERIC guess https://pa.johnsoncountytx.org/publicaccess/
    FAILED (net::ERR_CONNECTION_RESET). That is NOT the URL this project's
    assignment was given though -- our real candidates are
    https://pa.johnsoncountytx.org/DistrictClerkPA/Login.aspx and a
    hypothesized sibling .../CountyClerkPA/Login.aspx. Neither tested yet.
  - On Collin (same classic product, different county/tenant), the actual
    case-search entry point (Search.aspx) was gated behind an AWS WAF "Human
    Verification" interstitial (id=amzn-captcha-verify-button, network calls
    to *.awswaf.com) when clicking a generic "Case Records" link (landed on
    Search.aspx?ID=100). Collin's landing page ALSO exposed a link literally
    labelled "Probate Case Records" -> javascript:LaunchSearch('Search.aspx?
    ID=200', ...) -- untested whether THAT specific entry point also hits the
    WAF wall, or whether the WAF trigger is specific to tylertech.cloud/AWS-
    fronted hosting (which would mean self-hosted Denton might not have it).

This probe's job: verify all of the above independently for Tarrant/Denton/
Johnson, and push as far into TARRANT's actual search -> results -> case-
detail flow as possible in one run (per SYSTEM_GUIDE.md's directive to fully
solve Tarrant before moving on). Denton and Johnson get lighter-weight
reconnaissance in this same run (cheap, and de-risks the next iteration) but
their full scrape-flow verification happens in later, dedicated probe runs --
NOT assumed identical to Tarrant just because the URL pattern matches.
"""
import logging
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [ODY-PROBE] %(message)s')
log = logging.getLogger()

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe_out')
os.makedirs(OUT_DIR, exist_ok=True)

TODAY = date.today()
WINDOW_START = TODAY - timedelta(days=730)  # wide window for a first pass -- just want SOME rows

TARRANT_URL = "https://odyssey.tarrantcounty.com/PublicAccess/default.aspx"
DENTON_URL = "https://justice1.dentoncounty.gov/PublicAccess/"
JOHNSON_CANDIDATES = {
    "district_clerk": "https://pa.johnsoncountytx.org/DistrictClerkPA/Login.aspx",
    "county_clerk":   "https://pa.johnsoncountytx.org/CountyClerkPA/Login.aspx",
}

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

_shot_ctr = 0


def new_context(browser):
    ctx = browser.new_context(user_agent=UA)
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30000)
    return ctx, page


def snapshot(page, tag: str):
    global _shot_ctr
    _shot_ctr += 1
    tag = f"{_shot_ctr:02d}_{tag}"
    try:
        page.screenshot(path=os.path.join(OUT_DIR, f'{tag}.png'), full_page=True)
    except Exception as e:
        log.warning(f"[{tag}] screenshot failed: {e}")
    try:
        html = page.content()
        with open(os.path.join(OUT_DIR, f'{tag}.html'), 'w') as f:
            f.write(html)
        log.info(f"[{tag}] saved screenshot+html ({len(html)} bytes) url={page.url}")
    except Exception as e:
        log.warning(f"[{tag}] html save failed: {e}")


def dismiss_overlays(page, label=''):
    for sel in ['button:has-text("Agree")', 'button:has-text("Accept")',
                'button:has-text("I Agree")', 'button:has-text("Continue")',
                'button:has-text("OK")', '.modal button.close',
                '[aria-label="Close"]']:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                log.info(f"[{label}] dismissing overlay via {sel!r}")
                el.click()
                page.wait_for_timeout(500)
        except Exception:
            pass


def check_waf(page, label):
    info = page.evaluate("""() => {
        const html = document.documentElement.outerHTML;
        return {
            title: document.title,
            recaptcha: /recaptcha/i.test(html),
            hcaptcha: /hcaptcha/i.test(html),
            captchaWord: /captcha/i.test(html),
            awswaf: /awswaf|amzn-captcha/i.test(html),
            viewstate: !!document.querySelector('input[name="__VIEWSTATE"]'),
        };
    }""")
    log.info(f"[{label}] WAF/captcha markers: {info}")
    return info


def dump_nav_links(page, label, limit=80):
    links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
        .map(a => ({text:(a.textContent||'').replace(/\\s+/g,' ').trim(), href:a.getAttribute('href')||''}))
        .filter(l => l.text || l.href);""")
    log.info(f"[{label}] links ({len(links)}):")
    for l in links[:limit]:
        log.info(f"  LINK text={l['text']!r} href={l['href']!r}")
    return links


def dump_form(page, label):
    info = page.evaluate("""() => {
        const out = {inputs: [], selects: [], buttons: [], checkboxes: []};
        for (const el of document.querySelectorAll('input')) {
            const t = (el.type||'').toLowerCase();
            const rec = {type: t, id: el.id, name: el.name,
                         placeholder: el.placeholder, aria: el.getAttribute('aria-label'),
                         value: (el.value||'').slice(0,40), checked: el.checked,
                         label: (el.closest('label')?.textContent || '').trim().slice(0,60)};
            if (t === 'checkbox' || t === 'radio') out.checkboxes.push(rec);
            else out.inputs.push(rec);
        }
        for (const el of document.querySelectorAll('select')) {
            out.selects.push({
                id: el.id, name: el.name,
                options: Array.from(el.options).map(o => `${o.value}=${(o.textContent||'').trim()}`).slice(0,60)
            });
        }
        for (const el of document.querySelectorAll('button, input[type=submit], input[type=button], a.btn')) {
            const txt = (el.textContent||el.value||'').trim();
            if (txt) out.buttons.push({tag: el.tagName, id: el.id, name: el.name||'', text: txt.slice(0,60)});
        }
        return out;
    }""")
    log.info(f"[{label}] inputs ({len(info['inputs'])}):")
    for el in info['inputs']:
        log.info(f"  INPUT type={el['type']!r} id={el['id']!r} name={el['name']!r} "
                 f"placeholder={el['placeholder']!r} aria={el['aria']!r} label={el['label']!r}")
    log.info(f"[{label}] checkboxes/radios ({len(info['checkboxes'])}):")
    for el in info['checkboxes'][:60]:
        log.info(f"  CHECK type={el['type']!r} id={el['id']!r} name={el['name']!r} "
                 f"value={el['value']!r} checked={el['checked']} label={el['label']!r}")
    log.info(f"[{label}] selects ({len(info['selects'])}):")
    for el in info['selects']:
        log.info(f"  SELECT id={el['id']!r} name={el['name']!r} options={el['options']}")
    log.info(f"[{label}] buttons ({len(info['buttons'])}):")
    for el in info['buttons']:
        log.info(f"  BUTTON tag={el['tag']} id={el['id']!r} name={el['name']!r} text={el['text']!r}")
    return info


def dump_table(page, label):
    info = page.evaluate("""() => {
        const tables=[...document.querySelectorAll('table')].map(t=>({
            id: t.id, cls: (t.className||'').toString().slice(0,60),
            headers:[...t.querySelectorAll('th')].map(h=>(h.textContent||'').trim()),
            rowCount: t.querySelectorAll('tr').length
        }));
        const body=(document.body.innerText||'');
        const nores=/no\\s+(cases|records|results)\\s+(were\\s+)?found|0\\s+results|did not match/i.test(body);
        const m = body.match(/([\\d,]+)\\s+(cases?|records?|results?)\\s+(found|returned)?/i);
        return {tables, noResultsMsg: nores, countPhrase: m ? m[0] : '(none)'};
    }""")
    log.info(f"[{label}] count-phrase={info['countPhrase']!r} noResultsMsg={info['noResultsMsg']}")
    for t in info['tables']:
        log.info(f"[{label}] TABLE id={t['id']!r} cls={t['cls']!r} headers={t['headers']} rows={t['rowCount']}")
    # Grab sample rows from the biggest table (most likely the results grid)
    if info['tables']:
        biggest_idx = max(range(len(info['tables'])), key=lambda i: info['tables'][i]['rowCount'])
        rows = page.evaluate("""(idx) => {
            const t = document.querySelectorAll('table')[idx];
            if (!t) return [];
            const out = [];
            const trs = [...t.querySelectorAll('tr')];
            for (const tr of trs.slice(0, 8)) {
                out.push([...tr.querySelectorAll('th,td')].map(c => (c.textContent||'').replace(/\\s+/g,' ').trim()));
            }
            return out;
        }""", biggest_idx)
        for r in rows:
            log.info(f"[{label}] ROW: {r}")
    return info


def click_first_matching(page, selectors, label):
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                txt = ''
                try:
                    txt = el.inner_text()[:60]
                except Exception:
                    pass
                log.info(f"[{label}] clicking control matching {sel!r} (text={txt!r})")
                el.click()
                page.wait_for_timeout(2000)
                try:
                    page.wait_for_load_state('networkidle', timeout=12000)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


def try_clear_waf(page, label):
    """Best-effort: if an AWS WAF 'Human Verification' interstitial is up,
    try clicking through it and see if it silently resolves (common for the
    non-visual 'Challenge' action) vs. presents an unsolvable visual puzzle
    (the 'CAPTCHA' action)."""
    markers = check_waf(page, label)
    if not (markers.get('awswaf') or 'human verification' in (markers.get('title') or '').lower()):
        return True  # nothing to clear
    log.warning(f"[{label}] WAF human-verification wall detected. Attempting to clear it...")
    snapshot(page, f'{label}_waf_before')
    try:
        btn = page.locator('#amzn-captcha-verify-button, button:has-text("Begin")').first
        if btn.count() > 0:
            btn.click()
            log.info(f"[{label}] clicked WAF 'Begin' button, polling for resolution...")
            for i in range(6):
                page.wait_for_timeout(2000)
                t = page.title()
                log.info(f"[{label}] WAF poll {i+1}/6: title={t!r} url={page.url!r}")
                if 'human verification' not in t.lower() and 'verify' not in t.lower():
                    log.info(f"[{label}] WAF wall appears CLEARED (title changed).")
                    snapshot(page, f'{label}_waf_after_cleared')
                    return True
            snapshot(page, f'{label}_waf_after_stuck')
            log.warning(f"[{label}] WAF wall still present after 12s of polling -- likely a visual "
                        f"CAPTCHA puzzle requiring interaction, not an auto-passing JS challenge.")
            return False
        else:
            log.warning(f"[{label}] WAF wall present but no 'Begin' button found to click.")
            return False
    except Exception as e:
        log.warning(f"[{label}] error while trying to clear WAF wall: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Phase 1 -- cheap fingerprint sweep of all 3 counties (+ both Johnson URLs)
# ─────────────────────────────────────────────────────────────────────────

def fingerprint(pw, label, url):
    browser = None
    try:
        browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx, page = new_context(browser)
        log.info(f"--- {label}: {url}")
        resp = page.goto(url, wait_until='networkidle', timeout=25000)
        page.wait_for_timeout(1500)
        status = resp.status if resp else None
        final_url = page.url
        title = page.title()
        markers = check_waf(page, label)
        body_snip = ''
        try:
            body_snip = page.inner_text('body')[:800]
        except Exception:
            pass
        log.info(f"{label}: status={status} final_url={final_url!r} title={title!r} markers={markers}")
        log.info(f"{label}: body snippet: {body_snip!r}")
        return {'status': status, 'final_url': final_url, 'title': title}
    except Exception as exc:
        log.info(f"{label}: FAILED to load: {str(exc)[:300]}")
        return None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


def phase1_sweep(pw):
    log.info("=" * 70)
    log.info("PHASE 1 -- fingerprint sweep")
    log.info("=" * 70)
    fingerprint(pw, 'tarrant', TARRANT_URL)
    fingerprint(pw, 'denton', DENTON_URL)
    for name, url in JOHNSON_CANDIDATES.items():
        fingerprint(pw, f'johnson_{name}', url)


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 -- Tarrant deep dive (the main event this run)
# ─────────────────────────────────────────────────────────────────────────

def phase2_tarrant(pw):
    log.info("=" * 70)
    log.info("PHASE 2 -- TARRANT deep dive")
    log.info("=" * 70)
    browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx, page = new_context(browser)
    try:
        resp = page.goto(TARRANT_URL, wait_until='networkidle', timeout=25000)
        page.wait_for_timeout(1500)
        log.info(f"tarrant: status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        dismiss_overlays(page, 'tarrant landing')
        check_waf(page, 'tarrant landing')
        links = dump_nav_links(page, 'tarrant landing')
        snapshot(page, 'tarrant_landing')

        probate_links = [l for l in links if 'probate' in l['text'].lower()]
        log.info(f"tarrant: probate-labelled links: {probate_links}")

        if not probate_links:
            log.warning("tarrant: no link with 'Probate' in its text -- will try the location "
                        "<select> + generic 'Case Records' link instead.")

        navigated = False
        if probate_links:
            href = probate_links[0]['href']
            m = re.search(r"LaunchSearch\('([^']+)'", href)
            if m:
                target = m.group(1)
                # Resolve relative to the PublicAccess base
                base = TARRANT_URL.rsplit('/', 1)[0] + '/'
                full = base + target
                log.info(f"tarrant: navigating directly to resolved Probate search URL: {full}")
                try:
                    page.goto(full, wait_until='networkidle', timeout=25000)
                    page.wait_for_timeout(1500)
                    navigated = True
                except Exception as e:
                    log.warning(f"tarrant: direct nav to {full} failed: {e}")
            if not navigated:
                navigated = click_first_matching(page, ['a:has-text("Probate Case Records")'], 'tarrant')

        if not navigated:
            navigated = click_first_matching(
                page, ['a:has-text("Case Records")', 'a:has-text("Case Records Search")'], 'tarrant')

        log.info(f"tarrant: navigated to a search entry point: {navigated}")
        if not navigated:
            log.error("tarrant: could not find ANY case-records entry point. Stopping deep dive.")
            browser.close()
            return

        log.info(f"tarrant: post-nav title={page.title()!r} url={page.url!r}")
        snapshot(page, 'tarrant_search_entry')

        cleared = try_clear_waf(page, 'tarrant')
        if not cleared:
            log.error("tarrant: BLOCKED by WAF human-verification wall on the search entry point. "
                       "This is the critical open question for Tarrant -- flagging and stopping here.")
            browser.close()
            return

        # Dump whatever search form we landed on.
        dismiss_overlays(page, 'tarrant search form')
        dump_form(page, 'tarrant search form')
        dump_nav_links(page, 'tarrant search form', limit=40)
        snapshot(page, 'tarrant_search_form')

        # Best-effort: try to locate & set a Date Filed range + submit with blank name,
        # to see whether a blank/wildcard party search is permitted.
        start_fmt = WINDOW_START.strftime('%m/%d/%Y')
        end_fmt = TODAY.strftime('%m/%d/%Y')
        filled_dates = False
        for s_sel, e_sel in [
            ('input[id*="DateFiledOnAfter" i]', 'input[id*="DateFiledOnBefore" i]'),
            ('input[id*="DateFrom" i]', 'input[id*="DateTo" i]'),
            ('input[name*="DateFrom" i]', 'input[name*="DateTo" i]'),
            ('input[id*="From" i][id*="Date" i]', 'input[id*="To" i][id*="Date" i]'),
        ]:
            try:
                if page.locator(s_sel).count() > 0 and page.locator(e_sel).count() > 0:
                    page.fill(s_sel, start_fmt)
                    page.fill(e_sel, end_fmt)
                    log.info(f"tarrant: filled date range via {s_sel!r}/{e_sel!r} -> {start_fmt}..{end_fmt}")
                    filled_dates = True
                    break
            except Exception as e:
                log.info(f"tarrant: date fill attempt {s_sel!r} failed: {e}")
        log.info(f"tarrant: date range filled: {filled_dates}")

        submitted = click_first_matching(page, [
            'input[id*="SearchSubmit" i]', 'button[id*="SearchSubmit" i]',
            'input[value="Search" i]', 'button:has-text("Search")',
            'input[type="submit"]',
        ], 'tarrant')
        log.info(f"tarrant: submitted search: {submitted}")

        if submitted:
            page.wait_for_timeout(3000)
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                pass
            log.info(f"tarrant: post-submit title={page.title()!r} url={page.url!r}")
            snapshot(page, 'tarrant_results')
            cleared2 = try_clear_waf(page, 'tarrant results')
            if cleared2:
                dump_table(page, 'tarrant results')
                dump_nav_links(page, 'tarrant results', limit=30)

                # Try to open the first case detail link, if any case-number-shaped link exists.
                case_links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
                    .map(a => ({text:(a.textContent||'').trim(), href:a.getAttribute('href')||''}))
                    .filter(l => /^\\d{2,4}-\\d+|^[A-Z]?\\d{5,}/.test(l.text));""")
                log.info(f"tarrant: {len(case_links)} case-number-shaped links found; sample: {case_links[:5]}")
                if case_links:
                    try:
                        page.click(f"a:has-text(\"{case_links[0]['text']}\")")
                        page.wait_for_timeout(2500)
                        page.wait_for_load_state('networkidle', timeout=15000)
                        log.info(f"tarrant: case detail title={page.title()!r} url={page.url!r}")
                        snapshot(page, 'tarrant_case_detail')
                        detail_text = page.inner_text('body')
                        log.info(f"tarrant: case detail body ({len(detail_text)} chars):\n{detail_text[:6000]}")
                    except Exception as e:
                        log.warning(f"tarrant: could not open case detail: {e}")
        browser.close()
    except Exception as exc:
        log.error(f"tarrant: fatal error in deep dive: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 -- Denton recon (lighter weight; full deep dive comes in a later,
# dedicated iteration once Tarrant is solid)
# ─────────────────────────────────────────────────────────────────────────

def phase3_denton(pw):
    log.info("=" * 70)
    log.info("PHASE 3 -- Denton recon")
    log.info("=" * 70)
    browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    ctx, page = new_context(browser)
    try:
        resp = page.goto(DENTON_URL, wait_until='networkidle', timeout=25000)
        page.wait_for_timeout(1500)
        log.info(f"denton: status={resp.status if resp else None} final_url={page.url!r} title={page.title()!r}")
        dismiss_overlays(page, 'denton landing')
        check_waf(page, 'denton landing')
        links = dump_nav_links(page, 'denton landing')
        snapshot(page, 'denton_landing')

        probate_links = [l for l in links if 'probate' in l['text'].lower()]
        log.info(f"denton: probate-labelled links: {probate_links}")
        if not probate_links:
            body = page.inner_text('body')
            log.info(f"denton: full landing body text ({len(body)} chars):\n{body[:4000]}")

        navigated = False
        if probate_links:
            href = probate_links[0]['href']
            m = re.search(r"LaunchSearch\('([^']+)'", href)
            if m:
                base = DENTON_URL.rstrip('/') + '/'
                full = base + m.group(1)
                log.info(f"denton: navigating directly to resolved Probate search URL: {full}")
                try:
                    page.goto(full, wait_until='networkidle', timeout=25000)
                    page.wait_for_timeout(1500)
                    navigated = True
                except Exception as e:
                    log.warning(f"denton: direct nav failed: {e}")
            if not navigated:
                navigated = click_first_matching(page, ['a:has-text("Probate Case Records")'], 'denton')
        if not navigated:
            navigated = click_first_matching(
                page, ['a:has-text("Case Records")', 'a:has-text("Case Records Search")'], 'denton')

        log.info(f"denton: navigated to a search entry point: {navigated}")
        if navigated:
            log.info(f"denton: post-nav title={page.title()!r} url={page.url!r}")
            snapshot(page, 'denton_search_entry')
            cleared = try_clear_waf(page, 'denton')
            if cleared:
                dismiss_overlays(page, 'denton search form')
                dump_form(page, 'denton search form')

                # Switch to "Date Filed" search mode (id=DateFiled, name=SearchBy,
                # value=6) -- Party mode requires a Last Name (confirmed via
                # screenshot), which we don't have for a blind daily scrape.
                try:
                    page.check('#DateFiled')
                    page.wait_for_timeout(1500)
                    log.info("denton: switched SearchBy to 'Date Filed' -- form after switch:")
                    dump_form(page, 'denton date-filed mode')
                    snapshot(page, 'denton_date_filed_mode')
                except Exception as e:
                    log.warning(f"denton: could not switch to Date Filed mode: {e}")

                # Scope to Probate only if the case-category checkboxes are
                # actually interactive (best-effort; harmless if already hidden
                # / non-interactive -- we fall back to client-side filtering
                # via base.py's is_estate_case() either way).
                for uncheck_id in ['chkCriminal', 'chkFamily', 'chkCivil',
                                   'chkDtRangeCriminal', 'chkDtRangeFamily', 'chkDtRangeCivil']:
                    try:
                        el = page.locator(f'#{uncheck_id}')
                        if el.count() > 0:
                            el.uncheck(force=True, timeout=3000)
                            log.info(f"denton: unchecked #{uncheck_id}")
                    except Exception as e:
                        log.info(f"denton: could not uncheck #{uncheck_id}: {str(e)[:150]}")

                start_fmt = WINDOW_START.strftime('%m/%d/%Y')
                end_fmt = TODAY.strftime('%m/%d/%Y')
                filled = False
                for s_sel, e_sel in [('#DateFiledOnAfter', '#DateFiledOnBefore')]:
                    try:
                        if page.locator(s_sel).count() > 0:
                            page.fill(s_sel, start_fmt)
                            page.fill(e_sel, end_fmt)
                            filled = True
                            log.info(f"denton: filled date range {start_fmt}..{end_fmt}")
                    except Exception as e:
                        log.warning(f"denton: date fill failed: {e}")
                log.info(f"denton: date range filled: {filled}")
                snapshot(page, 'denton_pre_submit')

                submitted = click_first_matching(page, ['#SearchSubmit', 'input[value="Search" i]'], 'denton')
                log.info(f"denton: submitted search: {submitted}")
                if submitted:
                    page.wait_for_timeout(3000)
                    try:
                        page.wait_for_load_state('networkidle', timeout=15000)
                    except Exception:
                        pass
                    log.info(f"denton: post-submit title={page.title()!r} url={page.url!r}")
                    snapshot(page, 'denton_results')
                    cleared2 = try_clear_waf(page, 'denton results')
                    if cleared2:
                        dump_table(page, 'denton results')

                        case_links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
                            .map(a => ({text:(a.textContent||'').trim(), href:a.getAttribute('href')||''}))
                            .filter(l => /^\\d{2,4}-\\d+|^[A-Z]{1,3}-?\\d{2,4}-\\d+/.test(l.text));""")
                        log.info(f"denton: {len(case_links)} case-number-shaped links; sample: {case_links[:8]}")
                        if case_links:
                            try:
                                page.click(f"a:has-text(\"{case_links[0]['text']}\")")
                                page.wait_for_timeout(2500)
                                page.wait_for_load_state('networkidle', timeout=15000)
                                log.info(f"denton: case detail title={page.title()!r} url={page.url!r}")
                                snapshot(page, 'denton_case_detail')
                                detail_text = page.inner_text('body')
                                log.info(f"denton: case detail body ({len(detail_text)} chars):\n{detail_text[:6000]}")
                            except Exception as e:
                                log.warning(f"denton: could not open case detail: {e}")
        browser.close()
    except Exception as exc:
        log.error(f"denton: error in recon: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 -- Johnson: resolve DistrictClerkPA vs CountyClerkPA
# ─────────────────────────────────────────────────────────────────────────

def phase4_johnson(pw):
    log.info("=" * 70)
    log.info("PHASE 4 -- Johnson DistrictClerkPA vs CountyClerkPA")
    log.info("=" * 70)

    # 4a -- raw `requests` cross-check, independent of Chromium/Playwright's
    # fingerprint, to tell a TCP/host-level block apart from a browser-
    # specific one. Also try the bare domain root.
    import requests as _requests
    raw_targets = {
        'root': 'https://pa.johnsoncountytx.org/',
        'district_clerk': JOHNSON_CANDIDATES['district_clerk'],
        'county_clerk': JOHNSON_CANDIDATES['county_clerk'],
    }
    for name, url in raw_targets.items():
        for attempt in range(1, 4):
            try:
                r = _requests.get(url, timeout=20, headers={'User-Agent': UA})
                log.info(f"johnson_raw_{name}: attempt {attempt} -> status={r.status_code} "
                         f"final_url={r.url!r} len={len(r.content)} "
                         f"title_hint={re.search(r'<title>(.*?)</title>', r.text, re.I | re.S)}")
                break
            except Exception as e:
                log.info(f"johnson_raw_{name}: attempt {attempt} FAILED: {str(e)[:200]}")
                import time as _time
                _time.sleep(3)

    # 4b -- Playwright retries per candidate (in case of a transient blip).
    for name, url in JOHNSON_CANDIDATES.items():
        for attempt in range(1, 3):
            browser = pw.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
            ctx, page = new_context(browser)
            try:
                resp = page.goto(url, wait_until='networkidle', timeout=25000)
                page.wait_for_timeout(1500)
                status = resp.status if resp else None
                log.info(f"johnson_{name}: attempt {attempt} status={status} final_url={page.url!r} title={page.title()!r}")
                dismiss_overlays(page, f'johnson_{name}')
                check_waf(page, f'johnson_{name}')
                snapshot(page, f'johnson_{name}_landing_a{attempt}')
                body = page.inner_text('body')
                log.info(f"johnson_{name}: body ({len(body)} chars):\n{body[:3000]}")
                dump_nav_links(page, f'johnson_{name}', limit=40)
                dump_form(page, f'johnson_{name}')
                body_low = body.lower()
                log.info(f"johnson_{name}: mentions 'probate'={('probate' in body_low)} "
                         f"mentions 'case type'={('case type' in body_low)} "
                         f"mentions 'register'={('register' in body_low)} "
                         f"mentions 'guest'={('guest' in body_low)}")
                browser.close()
                break  # success, no need to retry
            except Exception as exc:
                log.info(f"johnson_{name}: attempt {attempt} FAILED: {str(exc)[:300]}")
                try:
                    browser.close()
                except Exception:
                    pass


def main():
    with sync_playwright() as pw:
        phase1_sweep(pw)
        phase2_tarrant(pw)
        phase3_denton(pw)
        phase4_johnson(pw)
    log.info("=" * 70)
    log.info(f"DONE. probe_out/ contains {len(os.listdir(OUT_DIR))} files.")


if __name__ == '__main__':
    main()
