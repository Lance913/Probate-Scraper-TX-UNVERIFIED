"""
Probe v1 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Preliminary recon (via WebFetch/WebSearch, NOT ground truth — this probe exists
to verify it) suggests:
  * Product is "Tyler Odyssey Portal" (branded that way in the page <title>),
    a different product from the classic on-prem "Odyssey Public Access"
    (/PublicAccess/default.aspx — also hosted on tylertech.cloud for some TX
    counties, e.g. Hale/Waller/Guadalupe, but NOT what Bexar runs).
  * Smart Search lives at /Portal/Home/Dashboard/29. Main box takes a case
    number or "Last, First Middle Suffix" name. An "Advanced" section adds:
    Filter by Location, Filter by Search Type, Party Search Criteria
    (Party Name/Nickname/Business Name/Sounds Like), Filter by Case Type,
    Filter by Case Status, Filter by File Date Start/End, Filter by Judicial
    Officer.
  * Case-type filtering is reportedly organized into categories: Civil
    Actions, Special Proceedings, ESTATES, Criminal Actions — "Estates" as
    its own top-level category is a strong signal for our filter, but the
    exact checkbox tree (does it include Guardianship/Mental Health as
    Estates sub-types, per base.py's caution?) is UNVERIFIED.
  * Registration NOT required for public data (no login wall expected).

This probe's job: verify/replace all of the above with ground truth. Dumps:
  1. Full form structure (every input/select/button/checkbox) at Smart Search,
     before and after trying to expand "Advanced".
  2. The case-type filter tree specifically (whatever shape it turns out to
     be — checkboxes, nested tree, multi-select, etc).
  3. Every JSON/XHR network response the SPA makes (URL + status + saved
     body) — if search/results are API-driven we want the raw shape.
  4. A best-effort real search submission (Estates-ish case types, wide file
     date window) and the resulting grid: headers/schema + sample rows, and
     whether decedent/executor/attorney data is already structured there.
  5. If a case number surfaces: opens the case detail page and dumps the
     Party Information section verbatim, to check whether executor name +
     mailing address are already indexed text (no OCR) or require deeper
     digging (e.g. a scanned application document).

Every phase is wrapped so a failure doesn't kill later phases — we want
partial ground truth even if one selector guess is wrong. Screenshots + raw
HTML + raw JSON bodies are all saved to probe_out/ and uploaded as a GitHub
Actions artifact so they can be pulled down and inspected directly, not just
grepped out of the run log.
"""
import json
import logging
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [BEXAR-PROBE] %(message)s')
log = logging.getLogger()

BASE = "https://portal-txbexar.tylertech.cloud/Portal"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'probe_out')
os.makedirs(OUT_DIR, exist_ok=True)

# Wide window for the first pass — we just want SOME results to see the grid
# schema; production window gets tuned later once we know real filing cadence.
TODAY = date(2026, 7, 28)
WINDOW_START = TODAY - timedelta(days=730)

_api_hits = []   # (method, url, status, content_type)
_saved_bodies = 0


def _safe_name(url: str) -> str:
    name = re.sub(r'[^A-Za-z0-9]+', '_', url)[-120:]
    return name


def dump_network(page):
    def on_response(resp):
        global _saved_bodies
        try:
            url = resp.url
            if '/Portal' not in url and 'tylertech' not in url:
                return
            ct = resp.headers.get('content-type', '')
            status = resp.status
            _api_hits.append((resp.request.method, url, status, ct))
            if 'json' in ct.lower() and _saved_bodies < 60:
                try:
                    body = resp.text()
                    if body and len(body) < 500_000:
                        fname = os.path.join(OUT_DIR, f'api_{_saved_bodies:03d}_{_safe_name(url)}.json')
                        with open(fname, 'w') as f:
                            f.write(body)
                        _saved_bodies += 1
                except Exception:
                    pass
        except Exception:
            pass
    page.on('response', on_response)


def dismiss_overlays(page, label=''):
    for sel in ['button:has-text("Agree")', 'button:has-text("Accept")',
                'button:has-text("I Agree")', 'button:has-text("Continue")',
                'button:has-text("OK")', '.modal button.close',
                '[aria-label="Close"]']:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                log.info(f"[{label}] Dismissing overlay via {sel!r}")
                el.click()
                page.wait_for_timeout(500)
        except Exception:
            pass


def snapshot(page, tag: str):
    """Save a screenshot + full HTML for later inspection, best-effort."""
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


def dump_form(page, label):
    info = page.evaluate("""() => {
        const out = {inputs: [], selects: [], buttons: [], checkboxes: [], iframes: []};
        for (const el of document.querySelectorAll('input')) {
            const t = (el.type||'').toLowerCase();
            const rec = {type: t, id: el.id, name: el.name,
                         placeholder: el.placeholder, aria: el.getAttribute('aria-label'),
                         value: el.value, checked: el.checked};
            if (t === 'checkbox' || t === 'radio') out.checkboxes.push(rec);
            else out.inputs.push(rec);
        }
        for (const el of document.querySelectorAll('select')) {
            out.selects.push({
                id: el.id, name: el.name,
                options: Array.from(el.options).map(o => ({value:o.value, text:(o.textContent||'').trim()})).slice(0,80)
            });
        }
        for (const el of document.querySelectorAll('button, a.btn, [role="button"], a[href="#"]')) {
            const txt = (el.textContent||'').trim();
            if (txt) out.buttons.push({tag: el.tagName, id: el.id, cls: (el.className||'').toString().slice(0,60), text: txt.slice(0,80)});
        }
        for (const el of document.querySelectorAll('iframe')) {
            out.iframes.push({src: el.src, id: el.id});
        }
        return out;
    }""")
    log.info(f"=== {label}: {len(info['inputs'])} text-inputs, {len(info['selects'])} selects, "
              f"{len(info['checkboxes'])} checkboxes, {len(info['buttons'])} buttons, "
              f"{len(info['iframes'])} iframes ===")
    for fr in info['iframes']:
        log.info(f"  IFRAME id={fr['id']!r} src={fr['src']!r}")
    for s in info['selects']:
        log.info(f"  SELECT id={s['id']!r} name={s['name']!r} options={s['options']}")
    for i in info['inputs']:
        log.info(f"  INPUT type={i['type']} id={i['id']!r} name={i['name']!r} "
                  f"placeholder={i['placeholder']!r} aria={i['aria']!r}")
    for c in info['checkboxes'][:150]:
        log.info(f"  {c['type'].upper()} id={c['id']!r} name={c['name']!r} "
                  f"value={c['value']!r} checked={c['checked']}")
    for b in info['buttons'][:80]:
        log.info(f"  BUTTON tag={b['tag']} id={b['id']!r} class={b['cls']!r} text={b['text']!r}")
    return info


def dump_body_text(page, label, max_lines=250):
    try:
        body_text = page.evaluate("() => document.body.innerText || ''")
    except Exception as e:
        log.warning(f"[{label}] body text failed: {e}")
        return
    lines = [l for l in body_text.split('\n') if l.strip()]
    log.info(f"=== {label}: BODY TEXT ({len(lines)} non-blank lines, showing up to {max_lines}) ===")
    for ln in lines[:max_lines]:
        log.info(f"  | {ln}")


def click_first(page, selectors, label):
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                log.info(f"[{label}] clicking via {sel!r}")
                el.click()
                page.wait_for_timeout(1200)
                return True
        except Exception as e:
            log.info(f"[{label}] selector {sel!r} failed: {e}")
    log.info(f"[{label}] no selector matched: {selectors}")
    return False


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ), viewport={'width': 1500, 'height': 1600})
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30000)
        dump_network(page)

        # ── Phase 1: landing page ──────────────────────────────────────────
        try:
            log.info(f"PHASE 1: GET {BASE}/")
            page.goto(f"{BASE}/", wait_until='networkidle')
            page.wait_for_timeout(1000)
            dismiss_overlays(page, 'landing')
            log.info(f"Landing page title: {page.title()!r} url={page.url}")
            snapshot(page, '01_landing')
        except Exception as ex:
            log.error(f"PHASE 1 error: {ex}", exc_info=True)

        # ── Phase 2: Smart Search dashboard ───────────────────────────────
        try:
            log.info(f"PHASE 2: GET {BASE}/Home/Dashboard/29 (Smart Search)")
            page.goto(f"{BASE}/Home/Dashboard/29", wait_until='networkidle')
            page.wait_for_timeout(1500)
            dismiss_overlays(page, 'smartsearch')
            log.info(f"Smart Search title: {page.title()!r} url={page.url}")
            snapshot(page, '02_smartsearch')
            dump_form(page, "Smart Search (initial)")
        except Exception as ex:
            log.error(f"PHASE 2 error: {ex}", exc_info=True)

        # ── Phase 3: expand Advanced options ──────────────────────────────
        try:
            click_first(page, ['text=Advanced', 'a:has-text("Advanced")',
                                'button:has-text("Advanced")',
                                '[aria-label*="Advanced" i]', '.advanced-search-toggle'],
                        'advanced-toggle')
            snapshot(page, '03_advanced')
            dump_form(page, "Smart Search (after Advanced click)")
        except Exception as ex:
            log.error(f"PHASE 3 error: {ex}", exc_info=True)

        # ── Phase 4: try to expand the Case Type / Case Category tree ─────
        try:
            click_first(page, ['text=Case Type', 'text=/Filter by Case Type/i',
                                '[aria-label*="Case Type" i]', 'text=Estates',
                                'text=/Case Categor/i'],
                        'case-type-toggle')
            snapshot(page, '04_casetype')
            dump_form(page, "Smart Search (after Case Type click)")
            dump_body_text(page, "Smart Search (after Case Type click)")
        except Exception as ex:
            log.error(f"PHASE 4 error: {ex}", exc_info=True)

        # ── Phase 5: attempt a real search submission ─────────────────────
        # Strategy: fill file-date range widely, try to check any checkbox
        # whose visible text/value looks estate-related, then submit. This is
        # a best-effort first pass; exact selectors get corrected in v2 based
        # on what phases 2-4 revealed above.
        try:
            log.info("PHASE 5: attempting a search submission")
            # Try date inputs by common id/name/aria patterns.
            date_start_sel = ['#FileDateStart', 'input[name*="FileDateStart" i]',
                               'input[aria-label*="File Date Start" i]',
                               'input[placeholder*="From" i]']
            date_end_sel = ['#FileDateEnd', 'input[name*="FileDateEnd" i]',
                             'input[aria-label*="File Date End" i]',
                             'input[placeholder*="To" i]']

            def fill_first(selectors, value, label):
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        if el.count() > 0 and el.is_visible():
                            el.fill(value)
                            log.info(f"[{label}] filled {sel!r} = {value!r}")
                            return True
                    except Exception:
                        pass
                log.info(f"[{label}] no date field matched {selectors}")
                return False

            fill_first(date_start_sel, WINDOW_START.strftime('%m/%d/%Y'), 'file-date-start')
            fill_first(date_end_sel, TODAY.strftime('%m/%d/%Y'), 'file-date-end')

            # Try checking any checkbox whose associated label text hints at
            # estates/probate case types, using a JS pass over label text
            # (works regardless of exact id naming).
            checked = page.evaluate("""() => {
                const hints = ['ESTATE','ADMINISTRATION','MUNIMENT','HEIRSHIP',
                               'PROBATE OF WILL','LETTERS TESTAMENTARY','DECEDENT'];
                const boxes = Array.from(document.querySelectorAll('input[type=checkbox]'));
                const hit = [];
                for (const b of boxes) {
                    let label = '';
                    if (b.id) {
                        const l = document.querySelector(`label[for="${b.id}"]`);
                        if (l) label = l.textContent || '';
                    }
                    if (!label && b.closest('label')) label = b.closest('label').textContent || '';
                    if (!label && b.parentElement) label = b.parentElement.textContent || '';
                    label = label.trim();
                    const up = label.toUpperCase();
                    if (hints.some(h => up.includes(h))) {
                        b.click();
                        hit.push(label.slice(0,80));
                    }
                }
                return hit;
            }""")
            log.info(f"Checkboxes checked via label-hint match: {checked}")
            snapshot(page, '05_before_submit')

            submitted = click_first(page, [
                'button[type="submit"]', 'button:has-text("Search")',
                'a:has-text("Search")', '#SmartSearchSubmit',
                '[aria-label*="Search" i][type="submit"]',
            ], 'submit-search')

            if submitted:
                page.wait_for_load_state('networkidle', timeout=20000)
                page.wait_for_timeout(3000)
                log.info(f"Post-submit URL: {page.url}")
                snapshot(page, '06_results')
                dump_body_text(page, "Results page", max_lines=300)

                # Dump any table(s) on the results page.
                tables = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('table')).map(t => ({
                        headers: Array.from(t.querySelectorAll('th')).map(h => (h.textContent||'').trim()),
                        rowCount: t.querySelectorAll('tr').length,
                        sample: Array.from(t.querySelectorAll('tr')).slice(1,8).map(
                            tr => Array.from(tr.querySelectorAll('td')).map(td => (td.textContent||'').trim())
                        ),
                    }));
                }""")
                for i, t in enumerate(tables):
                    log.info(f"TABLE {i}: headers={t['headers']} rowCount={t['rowCount']}")
                    for r in t['sample']:
                        log.info(f"  ROW: {r}")

                # Also look for a non-<table> results grid (div-based grids are
                # common in Angular/Knockout SPAs) — dump likely row containers.
                grid_rows = page.evaluate("""() => {
                    const candidates = document.querySelectorAll(
                        '[class*="result" i] [class*="row" i], [class*="grid" i] [class*="row" i], .case-list-item, [class*="search-result" i]'
                    );
                    return Array.from(candidates).slice(0,10).map(el => (el.textContent||'').replace(/\\s+/g,' ').trim().slice(0,300));
                }""")
                log.info(f"Non-table grid row candidates ({len(grid_rows)}):")
                for r in grid_rows:
                    log.info(f"  GRIDROW: {r}")

                # Try to find a case-number-looking link to drill into.
                case_links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({href: a.getAttribute('href'), text:(a.textContent||'').trim()}))
                        .filter(a => a.text && /[0-9]{2,}/.test(a.text))
                        .slice(0, 20);
                }""")
                log.info(f"Candidate case-number links ({len(case_links)}):")
                for cl in case_links:
                    log.info(f"  LINK text={cl['text']!r} href={cl['href']!r}")

                # ── Phase 6: open the first case-looking link, dump detail page ──
                if case_links:
                    try:
                        href = case_links[0]['href']
                        detail_url = href if href.startswith('http') else (BASE.rsplit('/Portal', 1)[0] + href if href.startswith('/') else href)
                        log.info(f"PHASE 6: opening case detail -> {detail_url}")
                        page.goto(detail_url, wait_until='networkidle')
                        page.wait_for_timeout(2500)
                        snapshot(page, '07_case_detail')
                        dump_body_text(page, "Case detail page", max_lines=300)
                    except Exception as ex:
                        log.error(f"PHASE 6 error: {ex}", exc_info=True)
                else:
                    log.info("No case-number links found to drill into for Phase 6.")
            else:
                log.warning("Could not find a Search submit control — dumping current DOM for manual inspection.")
                dump_form(page, "Smart Search (submit not found)")

        except Exception as ex:
            log.error(f"PHASE 5 error: {ex}", exc_info=True)

        # ── Final: dump all captured network hits ──────────────────────────
        log.info(f"=== NETWORK: {len(_api_hits)} responses touching /Portal or tylertech ===")
        for method, url, status, ct in _api_hits[:150]:
            log.info(f"  {method} {status} [{ct}] {url}")

        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _api_hits], f, indent=2)

        browser.close()
        log.info("Probe complete.")


if __name__ == '__main__':
    main()
