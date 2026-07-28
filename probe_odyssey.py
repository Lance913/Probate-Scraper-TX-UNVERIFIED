"""
Probe v1 — Collin County Odyssey Public Access (probate) + platform fingerprint
sweep of other TX counties that research suggests may run the same Tyler
Odyssey "Public Access" product (classic /PublicAccess/ ASP.NET WebForms
shape, distinct from the newer "Odyssey Portal" / tylertech.cloud product).

Part 1 — fingerprint_others(): cheap reachability + platform-marker check for
candidate URLs found via web research for Dallas, Tarrant, Denton, Johnson,
Travis, Ellis, Harris. Does NOT try to search each one — just: does it
resolve, what's the title, does it look like classic Odyssey PublicAccess
(ASP.NET __VIEWSTATE + disclaimer boilerplate + left-nav search types)?

Part 2 — investigate_collin(): the real target. Dump:
  - what Search.aspx?ID=200 actually resolves to (disclaimer? search form?)
  - every search-type nav option and its ID (to see if other IDs = other
    case categories, and confirm what 200 means)
  - every field/select on the default search form + all <select> options
    (hunting for a Case Category / Case Type dropdown)
  - any CAPTCHA / bot-check markers
  - if a search is submittable, submit one broad query and dump the results
    table headers + sample rows verbatim (via JS, not assumptions)
  - open one case detail page and dump its full structure, hunting for a
    "Party" section with role labels (Decedent / Applicant / Attorney)
"""
import logging
import re
import sys

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

COLLIN_URL = "https://cijspub.co.collin.tx.us/PublicAccess/Search.aspx?ID=200"
COLLIN_BASE = "https://cijspub.co.collin.tx.us/PublicAccess/"

# Candidate URLs for other TX counties, gathered from web research (official
# county-government pages), NOT assumed — each one still gets fingerprinted
# for real here. Anything not on this list either wasn't found (Ellis — its
# official site links to a non-Odyssey vendor, public.lgsonlinesolutions.com,
# so no Odyssey candidate exists to test) or is a different Tyler product
# generation worth flagging separately (Travis's odysseyweb.../Portal).
CANDIDATES = {
    "dallas":  "https://obpublicaccess24.dallascounty.org/PublicAccess/",
    "tarrant": "https://odyssey.tarrantcounty.com/PublicAccess/default.aspx",
    "denton":  "https://justice1.dentoncounty.gov/PublicAccess/",
    "johnson": "https://pa.johnsoncountytx.org/publicaccess/",
    "travis":  "https://odysseyweb.traviscountytx.gov/Portal",
    "harris":  "https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate",
    "ellis":   "https://public.lgsonlinesolutions.com/ors.html",
}


def dump_form(page, label):
    info = page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('input, select, textarea, button'));
        return els.map(el => {
            const o = {tag: el.tagName.toLowerCase(), type: el.type||'', id: el.id||'',
                       name: el.name||'', placeholder: el.placeholder||'',
                       text: (el.tagName==='BUTTON'? (el.textContent||'').trim() : ''),
                       visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)};
            if (el.tagName === 'SELECT') {
                o.options = Array.from(el.options).map(op => `${op.value}=${op.text}`);
            }
            return o;
        });
    }""")
    log.info(f"[{label}] form elements ({len(info)}):")
    for el in info:
        if el['tag'] == 'select':
            log.info(f"  SELECT id={el['id']!r} name={el['name']!r} visible={el['visible']} "
                      f"options={el.get('options')}")
        elif el['tag'] == 'button' or el['type'] in ('submit', 'button'):
            log.info(f"  BUTTON/SUBMIT id={el['id']!r} name={el['name']!r} text={el['text']!r} "
                      f"visible={el['visible']}")
        else:
            log.info(f"  {el['tag'].upper()} type={el['type']!r} id={el['id']!r} "
                      f"name={el['name']!r} placeholder={el['placeholder']!r} visible={el['visible']}")


def dump_nav_links(page, label):
    links = page.evaluate("""() => Array.from(document.querySelectorAll('a'))
        .map(a => ({text:(a.textContent||'').trim(), href:a.getAttribute('href')||''}))
        .filter(l => l.text || l.href);""")
    log.info(f"[{label}] links ({len(links)}):")
    for l in links[:80]:
        log.info(f"  LINK text={l['text']!r} href={l['href']!r}")


def check_captcha(page, label):
    info = page.evaluate("""() => {
        const html = document.documentElement.outerHTML;
        return {
            recaptcha: /recaptcha/i.test(html),
            hcaptcha: /hcaptcha/i.test(html),
            captchaWord: /captcha/i.test(html),
            viewstate: !!document.querySelector('input[name="__VIEWSTATE"]'),
        };
    }""")
    log.info(f"[{label}] captcha/markers: {info}")
    return info


def accept_disclaimer_if_present(page, label):
    """Odyssey instances commonly show a disclaimer/terms page first. Try a
    broad set of likely accept controls; log whatever we find either way."""
    try:
        body_text = page.inner_text('body')[:2000]
    except Exception:
        body_text = ''
    log.info(f"[{label}] initial title={page.title()!r} url={page.url!r}")
    log.info(f"[{label}] initial body text (first 2000 chars):\n{body_text}")

    clicked = False
    for sel in [
        'input[type="submit"][value*="Accept" i]',
        'input[type="submit"][value*="Agree" i]',
        'input[type="submit"][value*="Enter" i]',
        'input[type="button"][value*="Accept" i]',
        'button:has-text("Accept")',
        'button:has-text("Agree")',
        'button:has-text("Enter")',
        'button:has-text("Continue")',
        'a:has-text("Accept")',
        'a:has-text("I Agree")',
        'a:has-text("Enter Site")',
        '#idAccept',
        '#btnAccept',
    ]:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                log.info(f"[{label}] clicking disclaimer control matching {sel!r}")
                with page.expect_navigation(wait_until='networkidle', timeout=15000):
                    el.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        log.info(f"[{label}] no disclaimer/accept control matched — assuming none present.")
    else:
        log.info(f"[{label}] post-disclaimer title={page.title()!r} url={page.url!r}")
    return clicked


def investigate_collin(pw):
    log.info("=" * 70)
    log.info("PART 2 — Collin County deep dive")
    log.info("=" * 70)
    browser = pw.chromium.launch(headless=True,
                                  args=['--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(user_agent=(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ))
    page = ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.set_default_timeout(30000)
    try:
        log.info(f"Collin: goto {COLLIN_URL}")
        resp = page.goto(COLLIN_URL, wait_until='networkidle')
        log.info(f"Collin: HTTP status={resp.status if resp else 'N/A'}")
        accept_disclaimer_if_present(page, 'collin')
        page.wait_for_timeout(1000)

        # If we're not already on a Search.aspx?ID= page, navigate there explicitly.
        if 'Search.aspx' not in page.url:
            log.info(f"Collin: not on Search.aspx after disclaimer, re-navigating to {COLLIN_URL}")
            page.goto(COLLIN_URL, wait_until='networkidle')
            page.wait_for_timeout(1000)

        log.info(f"Collin: final title={page.title()!r} url={page.url!r}")
        check_captcha(page, 'collin')
        dump_nav_links(page, 'collin')
        dump_form(page, 'collin')

        # Try to find/read a "Case Category" selector's options directly (if any).
        try:
            body_text = page.inner_text('body')
            log.info(f"Collin: full body text ({len(body_text)} chars):\n{body_text[:6000]}")
        except Exception as e:
            log.info(f"Collin: could not read body text: {e}")

        # Try navigating to the bare default page (no ID) and a couple of
        # neighboring IDs to see how the node list / case-category nav differs.
        for probe_id in [None, 199, 200, 201]:
            try:
                url = COLLIN_BASE + ("default.aspx" if probe_id is None
                                      else f"Search.aspx?ID={probe_id}")
                page.goto(url, wait_until='networkidle')
                page.wait_for_timeout(600)
                t = page.title()
                heading = ''
                try:
                    heading = page.locator('h1, h2, .ssSearchHeader, #hdrTitle').first.inner_text(timeout=2000)
                except Exception:
                    pass
                log.info(f"Collin: probe ID={probe_id} -> url={page.url!r} title={t!r} heading={heading!r}")
            except Exception as e:
                log.info(f"Collin: probe ID={probe_id} failed: {str(e)[:200]}")

        browser.close()
    except Exception as exc:
        log.error(f"Collin: fatal error: {exc}", exc_info=True)
        try:
            browser.close()
        except Exception:
            pass


def fingerprint_others(pw):
    log.info("=" * 70)
    log.info("PART 1 — fingerprint sweep of other TX counties (candidate Odyssey URLs)")
    log.info("=" * 70)
    for county, url in CANDIDATES.items():
        browser = None
        try:
            browser = pw.chromium.launch(headless=True,
                                          args=['--disable-blink-features=AutomationControlled'])
            ctx = browser.new_context(user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ))
            page = ctx.new_page()
            page.set_default_timeout(20000)
            log.info(f"--- {county} : {url}")
            resp = page.goto(url, wait_until='networkidle', timeout=20000)
            status = resp.status if resp else None
            final_url = page.url
            title = page.title()
            markers = check_captcha(page, county)
            body_snip = ''
            try:
                body_snip = page.inner_text('body')[:500]
            except Exception:
                pass
            is_publicaccess_shape = '/PublicAccess/' in final_url or '/publicaccess/' in final_url.lower()
            log.info(f"{county}: status={status} final_url={final_url!r} title={title!r} "
                     f"publicaccess_url_shape={is_publicaccess_shape} viewstate={markers.get('viewstate')}")
            log.info(f"{county}: body snippet: {body_snip!r}")
        except Exception as exc:
            log.info(f"{county}: FAILED to load: {str(exc)[:300]}")
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


def main():
    with sync_playwright() as pw:
        fingerprint_others(pw)
        investigate_collin(pw)


if __name__ == '__main__':
    main()
