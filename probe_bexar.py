"""
Probe v2 — Bexar County Tyler Odyssey Portal (portal-txbexar.tylertech.cloud).

Ground truth from v1 (confirmed, not guessed):
  * Product: "Bexar County Justice Portal" (Tyler Odyssey Portal). Smart
    Search lives at /Portal/Home/Dashboard/29.
  * The whole form is Kendo UI (Telerik) widgets bound to hidden ASP.NET
    MVC-style model fields under `caseCriteria.*` — there are ZERO native
    <select> elements. "Filter by Location", "Filter by Search Type",
    "Filter by Case Type", "Filter by Case Status", "Filter by Judicial
    Officer" are all Kendo dropdown/multiselect widgets rendered from JS,
    not clickable-by-label-text (v1's label clicks 30s-timed-out).
  * Real form fields confirmed: caseCriteria.NameLast/NameFirst/NameMiddle,
    caseCriteria.CourtLocation, caseCriteria.CaseType, caseCriteria.CaseStatus,
    caseCriteria.FileDateStart, caseCriteria.FileDateEnd,
    caseCriteria.JudicialOfficer, caseCriteria.SearchCriteria (the single
    "record number or name" box), caseCriteria.SearchByPartyName/NickName/
    BusinessName/UseSoundex (radio-like hidden flags), caseCriteria.SearchCases
    (checked by default). Real submit control: input#btnSSSubmit[type=submit].
  * A Google reCAPTCHA v2 iframe + a Settings.CaptchaEnabled hidden field are
    present on the page — v1 didn't check the actual value. UNVERIFIED
    whether it blocks anonymous searches; this probe checks the value and
    watches for a captcha-related error after a real submit attempt.
  * v1's submit click matched the wrong element (hit "Smart Search" heading
    link, not the real button) and what got captured afterward was a leftover
    Kendo DatePicker calendar popup (table headers Su/Mo/Tu/.../Sa, day-number
    links) rendered on top of/instead of results — NOT actual search results.
    No JSON/XHR appeared in the network log at all in v1, confirming the real
    search never actually fired.

This probe fixes all of the above:
  1. Introspects EVERY Kendo widget on the page via its JS API
     ($(el).data('kendoXxx')) instead of clicking — dumps widget type + full
     dataSource for list-type widgets (Location, Case Type, Case Status,
     Judicial Officer options), which should reveal the real case-type
     taxonomy (is "Estates" really a top-level category? what are its
     children? does it include Guardianship/Mental Health?).
  2. Logs hidden-input VALUES too (v1 only logged id/name), specifically to
     read Settings.CaptchaEnabled.
  3. Fills File Date Start/End, then presses Escape to close any Kendo
     DatePicker popup before continuing (v1 left one open).
  4. Clicks the exact real submit control (#btnSSSubmit), not a fuzzy
     text-based selector.
  5. Polls for a real results signal after submit (Kendo Grid widget
     appearing with data, OR an explicit no-results/error/captcha message)
     instead of one fixed wait — SYSTEM_GUIDE.md bug #1 (slow tables read as
     empty).
  6. If a Kendo Grid widget is found, dumps its dataSource.data() directly
     via JS (structured JSON, no DOM-scraping guesswork) — this is likely
     the actual results delivery mechanism for this product.
  7. Saves the raw text of smartSearchPortlet.js (and other ePortal scripts)
     as artifacts AND greps them in-log for endpoint URLs / "CaseType" /
     "recaptcha" / "Estates" references, since reading the client's own
     source is more reliable than guessing its network contract.
  8. Only follows case-detail links that look like real hrefs (v1 crashed
     trying to Page.goto('#')).
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

TODAY = date(2026, 7, 28)
WINDOW_START = TODAY - timedelta(days=730)

_net_hits = []   # (method, url, status, content_type)
_saved_bodies = 0

_SCRIPT_SAVE_PATTERNS = ('smartsearchportlet', 'main.js', 'eportal.js')


def _safe_name(url: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '_', url)[-120:]


def dump_network(page):
    def on_response(resp):
        global _saved_bodies
        try:
            url = resp.url
            if 'tylertech' not in url:
                return
            ct = resp.headers.get('content-type', '')
            status = resp.status
            _net_hits.append((resp.request.method, url, status, ct))
            lower = url.lower()
            is_json = 'json' in ct.lower()
            is_target_script = any(p in lower for p in _SCRIPT_SAVE_PATTERNS)
            if (is_json or is_target_script) and _saved_bodies < 80:
                try:
                    body = resp.text()
                    if body and len(body) < 2_000_000:
                        ext = 'json' if is_json else 'js'
                        fname = os.path.join(OUT_DIR, f'net_{_saved_bodies:03d}_{_safe_name(url)}.{ext}')
                        with open(fname, 'w') as f:
                            f.write(body)
                        _saved_bodies += 1
                        if is_target_script:
                            _grep_script(url, body)
                except Exception:
                    pass
        except Exception:
            pass
    page.on('response', on_response)


def _grep_script(url: str, body: str):
    """Print lines from a client script that hint at the search API contract."""
    keywords = ['url:', 'ajax', 'CaseType', 'recaptcha', 'Recaptcha', 'Estates',
                'transport', 'read:', 'SmartSearch', 'dataSource', '.get(', '.post(']
    lines = body.split('\n')
    hits = [(i, ln) for i, ln in enumerate(lines)
            if any(k in ln for k in keywords)]
    log.info(f"=== SCRIPT GREP {url} ({len(lines)} lines, {len(hits)} keyword hits) ===")
    for i, ln in hits[:120]:
        log.info(f"  L{i}: {ln.strip()[:200]}")


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
        const out = {inputs: [], selects: [], buttons: [], checkboxes: []};
        for (const el of document.querySelectorAll('input')) {
            const t = (el.type||'').toLowerCase();
            const rec = {type: t, id: el.id, name: el.name,
                         placeholder: el.placeholder, aria: el.getAttribute('aria-label'),
                         value: el.value, checked: el.checked};
            if (t === 'checkbox' || t === 'radio') out.checkboxes.push(rec);
            else out.inputs.push(rec);
        }
        return out;
    }""")
    log.info(f"=== {label}: {len(info['inputs'])} text/hidden inputs, {len(info['checkboxes'])} checkboxes ===")
    for i in info['inputs']:
        log.info(f"  INPUT type={i['type']} id={i['id']!r} name={i['name']!r} "
                  f"value={i['value']!r} placeholder={i['placeholder']!r}")
    for c in info['checkboxes'][:150]:
        log.info(f"  {c['type'].upper()} id={c['id']!r} name={c['name']!r} "
                  f"value={c['value']!r} checked={c['checked']}")


def dump_kendo_widgets(page, label):
    """Introspect every Kendo widget on the page via its JS API — the
    reliable way to read dropdown/multiselect options on this product,
    since they're not native <select> elements and DOM clicking on them
    timed out in v1."""
    try:
        widgets = page.evaluate("""() => {
            if (typeof jQuery === 'undefined') return {error: 'no jQuery'};
            const out = [];
            const all = jQuery('[data-role], input, div, span').toArray();
            const seen = new Set();
            for (const el of all) {
                const $el = jQuery(el);
                const data = $el.data();
                if (!data) continue;
                for (const key of Object.keys(data)) {
                    if (!key.startsWith('kendo')) continue;
                    const widget = data[key];
                    if (!widget || typeof widget !== 'object') continue;
                    const uid = widget._guid || (el.id + '|' + key);
                    if (seen.has(uid)) continue;
                    seen.add(uid);
                    const rec = {
                        widgetType: key,
                        elId: el.id || '',
                        elName: el.getAttribute('name') || '',
                        options: null,
                        value: null,
                    };
                    try { rec.value = widget.value ? widget.value() : null; } catch (e) {}
                    try {
                        if (widget.dataSource && widget.dataSource.data) {
                            const items = widget.dataSource.data();
                            rec.options = items.slice(0, 300).map(it => {
                                try { return it.toJSON ? it.toJSON() : it; } catch (e) { return String(it); }
                            });
                            rec.optionCount = items.length;
                        }
                    } catch (e) { rec.dsError = String(e); }
                    out.push(rec);
                }
            }
            return out;
        }""")
        if isinstance(widgets, dict) and widgets.get('error'):
            log.warning(f"[{label}] Kendo introspection failed: {widgets['error']}")
            return
        log.info(f"=== {label}: {len(widgets)} Kendo widgets found ===")
        for w in widgets:
            log.info(f"  WIDGET type={w['widgetType']} elId={w['elId']!r} elName={w['elName']!r} "
                      f"value={w['value']!r} optionCount={w.get('optionCount')}")
            if w.get('options'):
                for opt in w['options'][:80]:
                    log.info(f"      OPT: {opt}")
            if w.get('dsError'):
                log.info(f"      dsError: {w['dsError']}")
    except Exception as e:
        log.error(f"[{label}] dump_kendo_widgets error: {e}", exc_info=True)


def dump_body_text(page, label, max_lines=250):
    try:
        body_text = page.evaluate("() => document.body.innerText || ''")
    except Exception as e:
        log.warning(f"[{label}] body text failed: {e}")
        return ''
    lines = [l for l in body_text.split('\n') if l.strip()]
    log.info(f"=== {label}: BODY TEXT ({len(lines)} non-blank lines, showing up to {max_lines}) ===")
    for ln in lines[:max_lines]:
        log.info(f"  | {ln}")
    return body_text


def wait_for_results_or_message(page, timeout_ms=25000):
    """Poll for a real results signal: a Kendo Grid with rows, OR explicit
    no-results/error/captcha text. Returns a dict describing what happened.
    Modeled on the reference system's _wait_for_results (SYSTEM_GUIDE.md bug
    #1 — a slow render must not be read as 0 results)."""
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        state = page.evaluate("""() => {
            let gridRows = -1;
            try {
                if (typeof jQuery !== 'undefined') {
                    const g = jQuery('[data-role="grid"]').data('kendoGrid');
                    if (g) gridRows = g.dataSource.data().length;
                }
            } catch (e) {}
            const body = (document.body.innerText || '');
            const noRes = /no results|no records|0 results|did not match|no matches/i.test(body);
            const captcha = /captcha|verify you are human|i'm not a robot|are you a robot/i.test(body);
            const err = /error occurred|something went wrong|unable to complete/i.test(body);
            const tableRows = document.querySelectorAll('table tr').length;
            return {gridRows, noRes, captcha, err, tableRows};
        }""")
        if state['gridRows'] > 0:
            return {'status': 'grid_has_rows', **state}
        if state['noRes'] or state['captcha'] or state['err']:
            return {'status': 'explicit_message', **state}
        page.wait_for_timeout(500)
    return {'status': 'timeout'}


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

        # ── Phase 1+2: Smart Search dashboard ──────────────────────────────
        try:
            log.info(f"PHASE 1-2: GET {BASE}/Home/Dashboard/29 (Smart Search)")
            page.goto(f"{BASE}/", wait_until='networkidle')
            page.wait_for_timeout(600)
            dismiss_overlays(page, 'landing')
            page.goto(f"{BASE}/Home/Dashboard/29", wait_until='networkidle')
            page.wait_for_timeout(1500)
            dismiss_overlays(page, 'smartsearch')
            log.info(f"Smart Search title: {page.title()!r} url={page.url}")
        except Exception as ex:
            log.error(f"PHASE 1-2 error: {ex}", exc_info=True)

        # ── Phase 3: expand Advanced, dump hidden-input values (captcha flag) ──
        try:
            page.locator('a:has-text("Advanced")').first.click()
            page.wait_for_timeout(1000)
            dump_form(page, "Smart Search (Advanced expanded, WITH values)")
        except Exception as ex:
            log.error(f"PHASE 3 error: {ex}", exc_info=True)

        # ── Phase 4: introspect every Kendo widget (Location / Case Type / etc) ──
        try:
            dump_kendo_widgets(page, "Smart Search widgets (before any interaction)")
            snapshot(page, '01_smartsearch_advanced')
        except Exception as ex:
            log.error(f"PHASE 4 error: {ex}", exc_info=True)

        # ── Phase 5: fill date range, close any stray calendar popup ──────
        try:
            log.info("PHASE 5: filling File Date Start/End")
            start_el = page.locator('input[name*="FileDateStart" i]').first
            end_el = page.locator('input[name*="FileDateEnd" i]').first
            if start_el.count() > 0:
                start_el.fill(WINDOW_START.strftime('%m/%d/%Y'))
                page.keyboard.press('Escape')
                page.wait_for_timeout(300)
            if end_el.count() > 0:
                end_el.fill(TODAY.strftime('%m/%d/%Y'))
                page.keyboard.press('Escape')
                page.wait_for_timeout(300)
            # Click a neutral heading to force-blur/close any lingering Kendo popup.
            try:
                page.locator('text=Case Search Criteria').first.click(timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(300)
            snapshot(page, '02_dates_filled')
        except Exception as ex:
            log.error(f"PHASE 5 error: {ex}", exc_info=True)

        # ── Phase 6: submit via the REAL button, then poll for real results ──
        try:
            log.info("PHASE 6: clicking the real #btnSSSubmit control")
            btn = page.locator('#btnSSSubmit').first
            log.info(f"btnSSSubmit count={btn.count()} visible={btn.is_visible() if btn.count() else 'n/a'}")
            btn.click()
            result = wait_for_results_or_message(page, timeout_ms=25000)
            log.info(f"Post-submit wait result: {result}")
            page.wait_for_timeout(1500)
            log.info(f"Post-submit URL: {page.url}")
            snapshot(page, '03_after_submit')
            body_text = dump_body_text(page, "Results page", max_lines=300)

            dump_kendo_widgets(page, "Widgets AFTER submit (looking for kendoGrid)")

            # Raw <table> dump too, in case results render as plain HTML.
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

            # Real hrefs only (skip '#'/empty) for a potential case-detail drill-in.
            case_links = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({href: a.getAttribute('href'), text:(a.textContent||'').trim()}))
                    .filter(a => a.href && a.href !== '#' && !a.href.startsWith('javascript'))
                    .slice(0, 30);
            }""")
            log.info(f"Real (non-#) links on results page ({len(case_links)}):")
            for cl in case_links:
                log.info(f"  LINK text={cl['text']!r} href={cl['href']!r}")

        except Exception as ex:
            log.error(f"PHASE 6 error: {ex}", exc_info=True)

        # ── Final: network summary ──────────────────────────────────────────
        log.info(f"=== NETWORK: {len(_net_hits)} responses touching tylertech ===")
        by_ct = {}
        for method, url, status, ct in _net_hits:
            by_ct.setdefault(ct, []).append(url)
        for ct, urls in by_ct.items():
            log.info(f"  content-type={ct!r}: {len(urls)} responses")
        non_static = [(m, u, s, c) for m, u, s, c in _net_hits
                      if not any(x in c for x in ('css', 'image', 'font', 'javascript'))
                      or 'json' in c]
        log.info(f"Non-static (candidate API) responses: {len(non_static)}")
        for m, u, s, c in non_static[:60]:
            log.info(f"  {m} {s} [{c}] {u}")

        with open(os.path.join(OUT_DIR, 'network_summary.json'), 'w') as f:
            json.dump([{'method': m, 'url': u, 'status': s, 'content_type': c}
                       for m, u, s, c in _net_hits], f, indent=2)

        browser.close()
        log.info("Probe v2 complete.")


if __name__ == '__main__':
    main()
