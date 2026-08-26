"""
Probe — Ellis County, TX probate case search via LGS Online Solutions
(public.lgsonlinesolutions.com/ors.html).

Why this URL, not a guess (SYSTEM_GUIDE.md §6/§8 fingerprinting done first):
- Ellis County's OFFICIAL site (elliscountytx.gov/1397/Online-Record-Search)
  explicitly separates two DIFFERENT systems:
    * "Property Search"       -> https://ellisccktxpublicsearch.us/AcclaimWeb/
      (a decoy — real-property/document recording, NOT court case search)
    * "County Court Record Search" -> https://public.lgsonlinesolutions.com/ors.html
      (this is the one we want)
- us-lgs.com/products/county-clerk (the vendor's own product page) confirms
  their DataPoint system covers "Probate/Guardianship Case Management" with
  "Public Inquiry for all systems" — probate should be in scope.
- Third-party summaries describe ors.html as a free, no-subscription-required
  INDEX search (registration/subscription is only needed to purchase document
  IMAGES) — i.e. exactly what we need (index data: name, case #, case type,
  filing date), no paywall expected for the scrape itself.
- A prior sweep (sibling agent's probe on add-collin-probate-scraper branch,
  run 30384956632, part of a multi-county fingerprint sweep — NOT built for
  Ellis specifically) already hit this exact URL from a real US GitHub
  Actions IP and got: HTTP 200, title="Online Records Search", no
  recaptcha/hcaptcha/viewstate markers — i.e. REACHABLE, not geo-blocked for
  US traffic. But `page.inner_text('body')` came back empty after hanging
  for exactly its 20s default timeout. That specific symptom (goto succeeds،
  title renders, but a `body` selector wait times out) is the classic
  signature of a page with NO <body> element Playwright can match — i.e. a
  classic HTML <frameset> page (old-school county-vendor systems still do
  this; the literal filename "ors.html", not an SPA route, is consistent
  with that). This probe is built to confirm/refute that hypothesis and,
  either way, get past it to the real search form.
- Both my own local WebFetch attempts at this URL got connect ECONNREFUSED
  (same symptom the human-supplied discovery notes reported) — consistent
  with geo-blocking non-US traffic. Real investigation has to happen on a
  GitHub Actions US runner, per SYSTEM_GUIDE.md §2.1 — this script is meant
  to be run there, not locally.

This probe is defensive and multi-hypothesis on purpose: it dumps many
diagnostics in one run (structure, frames, screenshot, raw HTML, console
errors, network calls) so a single ~2min iteration yields maximum signal.
"""
import logging
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [PROBE] %(message)s')
log = logging.getLogger()

ORS_URL = "https://public.lgsonlinesolutions.com/ors.html"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def check_structure(target, label):
    try:
        info = target.evaluate("""() => ({
            hasBody: !!document.body,
            hasFrameset: !!document.querySelector('frameset'),
            frameCount: document.querySelectorAll('frame').length,
            iframeCount: document.querySelectorAll('iframe').length,
            frameSrcs: Array.from(document.querySelectorAll('frame,iframe')).map(
                f => (f.getAttribute('src')||'') + ' name=' + (f.getAttribute('name')||'')),
            scripts: Array.from(document.querySelectorAll('script[src]')).map(
                s => s.getAttribute('src')).slice(0,25),
            title: document.title,
            htmlLen: document.documentElement ? document.documentElement.outerHTML.length : 0,
            bodyChildCount: document.body ? document.body.children.length : -1,
        })""")
        log.info(f"[{label}] structure: {info}")
        return info
    except Exception as e:
        log.info(f"[{label}] check_structure FAILED: {str(e)[:300]}")
        return {}


def safe_body_text(target, label, limit=3000):
    try:
        txt = target.inner_text('body', timeout=8000)
        log.info(f"[{label}] body text ({len(txt)} chars): {txt[:limit]!r}")
        return txt
    except Exception as e:
        log.info(f"[{label}] inner_text('body') FAILED/timeout: {str(e)[:200]}")
        try:
            txt2 = target.evaluate(
                "() => (document.documentElement && "
                "(document.documentElement.innerText || document.documentElement.textContent)) "
                "|| 'NO DOCUMENT ELEMENT TEXT'")
            log.info(f"[{label}] documentElement text fallback ({len(txt2)} chars): {txt2[:limit]!r}")
            return txt2
        except Exception as e2:
            log.info(f"[{label}] documentElement fallback ALSO failed: {str(e2)[:200]}")
            return ''


def dump_form(target, label):
    try:
        info = target.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('input, select, textarea, button, a'));
            return els.slice(0, 250).map(el => {
                const o = {tag: el.tagName.toLowerCase(), type: el.type||'', id: el.id||'',
                           name: el.name||'', placeholder: el.placeholder||'',
                           text: (el.textContent||'').trim().slice(0,80),
                           href: (el.getAttribute && el.getAttribute('href')) || '',
                           visible: !!(el.offsetWidth || el.offsetHeight ||
                                       (el.getClientRects && el.getClientRects().length))};
                if (el.tagName === 'SELECT') {
                    o.options = Array.from(el.options).map(op => `${op.value}=${op.text}`);
                }
                return o;
            });
        }""")
    except Exception as e:
        log.info(f"[{label}] dump_form FAILED: {str(e)[:300]}")
        return
    log.info(f"[{label}] form/nav elements ({len(info)}):")
    for el in info:
        if el['tag'] == 'select':
            log.info(f"  SELECT id={el['id']!r} name={el['name']!r} visible={el['visible']} "
                      f"options={el.get('options')}")
        elif el['tag'] in ('button', 'a') or el['type'] in ('submit', 'button'):
            log.info(f"  {el['tag'].upper()} id={el['id']!r} name={el['name']!r} text={el['text']!r} "
                      f"href={el.get('href','')!r} visible={el['visible']}")
        else:
            log.info(f"  {el['tag'].upper()} type={el['type']!r} id={el['id']!r} name={el['name']!r} "
                      f"placeholder={el['placeholder']!r} visible={el['visible']}")


def dump_frames(page):
    frames = page.frames
    log.info(f"Frames on page ({len(frames)} total, including main):")
    for i, fr in enumerate(frames):
        try:
            log.info(f"  frame[{i}]: name={fr.name!r} url={fr.url!r}")
        except Exception as e:
            log.info(f"  frame[{i}]: FAILED to read: {e}")
    return frames


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(user_agent=UA, viewport={'width': 1440, 'height': 1000})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        console_msgs = []
        page.on('console', lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
        page.on('pageerror', lambda e: console_msgs.append(f"PAGEERROR: {str(e)[:200]}"))

        xhr_log = []

        def on_response(resp):
            try:
                ct = resp.headers.get('content-type', '')
                if 'json' in ct or '/api/' in resp.url.lower() or resp.request.method == 'POST':
                    xhr_log.append((resp.request.method, resp.status, resp.url))
            except Exception:
                pass
        page.on('response', on_response)

        log.info(f"Navigating to {ORS_URL} (wait_until='load')")
        try:
            resp = page.goto(ORS_URL, wait_until='load', timeout=30000)
            log.info(f"goto returned status={resp.status if resp else None} ok={resp.ok if resp else None}")
        except Exception as e:
            log.error(f"goto FAILED even with wait_until='load': {str(e)[:400]}")
            resp = None

        page.wait_for_timeout(4000)  # let any late JS/redirect settle
        log.info(f"Final URL: {page.url!r} | Title: {page.title()!r}")

        try:
            page.screenshot(path='ors_landing.png', full_page=True)
            log.info("Saved screenshot -> ors_landing.png")
        except Exception as e:
            log.info(f"Screenshot FAILED: {str(e)[:200]}")

        try:
            html = page.content()
            with open('ors_landing.html', 'w') as f:
                f.write(html)
            log.info(f"Saved raw HTML ({len(html)} chars) -> ors_landing.html")
        except Exception as e:
            log.info(f"page.content() FAILED: {str(e)[:200]}")

        check_structure(page, 'main-doc')
        safe_body_text(page, 'main-doc')
        dump_form(page, 'main-doc')
        frames = dump_frames(page)

        # Drill into every non-main frame (handles classic <frameset> pages,
        # where the real content/form lives inside a child frame, not <body>).
        for i, fr in enumerate(frames):
            if fr == page.main_frame:
                continue
            label = f"frame[{i}]:{fr.url}"
            try:
                fr.wait_for_load_state('load', timeout=8000)
            except Exception:
                pass
            check_structure(fr, label)
            safe_body_text(fr, label)
            dump_form(fr, label)
            try:
                fr_html = fr.content()
                fname = f'ors_frame_{i}.html'
                with open(fname, 'w') as f:
                    f.write(fr_html)
                log.info(f"[{label}] saved raw HTML ({len(fr_html)} chars) -> {fname}")
            except Exception as e:
                log.info(f"[{label}] frame.content() FAILED: {str(e)[:200]}")

        log.info(f"Console/page errors captured ({len(console_msgs)}):")
        for m in console_msgs[:60]:
            log.info(f"  {m}")

        # Detect (don't yet act on) common guest/search entry points, in main
        # doc and every frame, so the NEXT probe iteration can target exact
        # selectors instead of guessing.
        targets = [('main', page)] + [(f'frame:{fr.url}', fr) for fr in frames if fr != page.main_frame]
        for tname, t in targets:
            for sel in ['text=/guest/i', 'text=/continue without/i', 'text=/new account/i',
                        'text=/log ?in/i', 'a:has-text("Search")', 'button:has-text("Search")',
                        'text=/online records search/i', 'text=/case search/i',
                        'text=/court records/i']:
                try:
                    loc = t.locator(sel).first
                    if loc.count() > 0:
                        log.info(f"[{tname}] candidate control matching {sel!r}: "
                                 f"text={loc.inner_text()[:100]!r}")
                except Exception:
                    pass

        log.info(f"XHR/interesting network responses observed ({len(xhr_log)}):")
        for m, s, u in xhr_log[:80]:
            log.info(f"  {m} {s} {u}")

        # ── PHASE 2 — Guest Login ───────────────────────────────────────────
        # ors_UserLogin.html's "Guest Login" button calls JS GuestSubmit(),
        # which sets OPERCODE=orguest / PASSWD=orguest then xSubmit()s the
        # form (POST /cgi-bin/webshell.asp). Fetch login.js first (same-origin
        # fetch from inside the page, so it rides the existing session/cookies)
        # to see exactly what xSubmit() does before triggering it blind.
        menu_frame = next((fr for fr in frames if fr.name == 'menu'), None)
        if not menu_frame:
            log.error("Could not find frame named 'menu' — aborting Phase 2.")
            browser.close()
            return

        log.info("=" * 70)
        log.info("PHASE 2 — Guest Login")
        log.info("=" * 70)

        try:
            js_src = menu_frame.evaluate(
                "() => fetch('/javascript4.4/login.js').then(r => r.text())")
            log.info(f"login.js contents ({len(js_src)} chars):\n{js_src[:6000]}")
        except Exception as e:
            log.info(f"Fetching login.js FAILED: {str(e)[:300]}")

        webshell_bodies = []

        def on_response2(resp):
            try:
                if 'webshell' in resp.url.lower():
                    ct = resp.headers.get('content-type', '')
                    try:
                        body = resp.text()
                    except Exception:
                        body = '<unreadable/binary>'
                    webshell_bodies.append((resp.status, resp.url, ct, body))
            except Exception:
                pass
        page.on('response', on_response2)

        clicked = False
        try:
            guest_btn = menu_frame.locator('#GuestLogIn')
            log.info(f"Guest Login button count={guest_btn.count()}")
            guest_btn.click(timeout=10000)
            clicked = True
            log.info("Clicked #GuestLogIn")
        except Exception as e:
            log.error(f"Click #GuestLogIn FAILED: {str(e)[:300]}")
            try:
                menu_frame.evaluate("GuestSubmit()")
                clicked = True
                log.info("Invoked GuestSubmit() directly via evaluate() as fallback")
            except Exception as e2:
                log.error(f"Direct GuestSubmit() evaluate ALSO failed: {str(e2)[:300]}")

        if not clicked:
            browser.close()
            return

        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state('load', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        log.info(f"[post-guest-login] webshell.asp responses captured ({len(webshell_bodies)}):")
        for status, url, ct, body in webshell_bodies:
            log.info(f"  status={status} url={url} content-type={ct!r}")
            log.info(f"  body ({len(body)} chars): {body[:4000]!r}")

        log.info(f"[post-guest-login] Final top URL: {page.url!r} title={page.title()!r}")
        try:
            page.screenshot(path='ors_after_guest_login.png', full_page=True)
            log.info("Saved screenshot -> ors_after_guest_login.png")
        except Exception as e:
            log.info(f"post-login screenshot FAILED: {str(e)[:200]}")

        frames2 = dump_frames(page)
        for i, fr in enumerate(frames2):
            label = f"post-login-frame[{i}]:{fr.url}"
            try:
                fr.wait_for_load_state('load', timeout=8000)
            except Exception:
                pass
            check_structure(fr, label)
            safe_body_text(fr, label, limit=5000)
            dump_form(fr, label)
            try:
                fr_html = fr.content()
                fname = f'ors_postlogin_frame_{i}.html'
                with open(fname, 'w') as f:
                    f.write(fr_html)
                log.info(f"[{label}] saved raw HTML ({len(fr_html)} chars) -> {fname}")
            except Exception as e:
                log.info(f"[{label}] frame.content() FAILED: {str(e)[:200]}")

        # ── PHASE 3 — Dismiss guest-message dialog, click into Search ──────
        # The 'update' frame post-login is a "Guest Login Message" panel
        # ("You can not purchase images without an account login.") with
        # Continue / Search (#actionButton3) / Logoff (#actionButton4)
        # buttons. Dismiss it, then click Search to reach the real case-
        # search form.
        log.info("=" * 70)
        log.info("PHASE 3 — Dismiss guest message, click into Search")
        log.info("=" * 70)

        def get_frame_by_name(nm):
            for fr in page.frames:
                if fr.name == nm:
                    return fr
            return None

        def dump_onclick(target, label):
            try:
                els = target.evaluate("""() => Array.from(document.querySelectorAll('[onclick]')).map(el => ({
                    tag: el.tagName.toLowerCase(), id: el.id||'', cls: el.className||'',
                    text: (el.textContent||'').trim().slice(0,60),
                    onclick: (el.getAttribute('onclick')||'').slice(0,150)
                }))""")
                log.info(f"[{label}] elements with onclick ({len(els)}):")
                for el in els[:80]:
                    log.info(f"  {el}")
            except Exception as e:
                log.info(f"[{label}] onclick dump FAILED: {str(e)[:200]}")

        update_frame = get_frame_by_name('update')
        menu_frame2 = get_frame_by_name('menu')
        if update_frame:
            dump_onclick(update_frame, 'update frame (pre-continue)')
        if menu_frame2:
            dump_onclick(menu_frame2, 'menu frame (pre-continue)')

        if not update_frame:
            log.error("Could not find frame named 'update' post-login — stopping before click.")
            browser.close()
            return

        try:
            update_frame.locator('#WTKCB_10').click(timeout=8000)
            log.info("Clicked #WTKCB_10 (Continue)")
        except Exception as e:
            log.error(f"Click #WTKCB_10 FAILED: {str(e)[:300]}")

        page.wait_for_timeout(4000)
        try:
            page.screenshot(path='ors_after_continue.png', full_page=True)
            log.info("Saved screenshot -> ors_after_continue.png")
        except Exception as e:
            log.info(f"screenshot FAILED: {str(e)[:200]}")

        # Re-find frames (may have been recreated) and try to click Search.
        clicked_search = False
        for fr_name in ('update', 'menu'):
            fr_try = get_frame_by_name(fr_name)
            if not fr_try:
                continue
            try:
                btn = fr_try.locator('#actionButton3')
                if btn.count() > 0:
                    btn.click(timeout=8000)
                    clicked_search = True
                    log.info(f"Clicked #actionButton3 (Search) in frame name={fr_name!r} url={fr_try.url!r}")
                    break
            except Exception as e:
                log.info(f"Click #actionButton3 in frame name={fr_name!r} failed: {str(e)[:200]}")

        if not clicked_search:
            log.error("Could not click a Search button by id=actionButton3 in update/menu frames.")

        page.wait_for_timeout(5000)
        try:
            page.wait_for_load_state('load', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        try:
            page.screenshot(path='ors_after_search_click.png', full_page=True)
            log.info("Saved screenshot -> ors_after_search_click.png")
        except Exception as e:
            log.info(f"screenshot FAILED: {str(e)[:200]}")

        frames3 = dump_frames(page)
        for i, fr in enumerate(frames3):
            label = f"phase3-frame[{i}]:{fr.name}:{fr.url}"
            st = check_structure(fr, label)
            if st.get('htmlLen', 999) < 60:
                continue  # skip known-empty heart.html-style placeholder frames
            safe_body_text(fr, label, limit=6000)
            dump_form(fr, label)
            dump_onclick(fr, label)
            try:
                fr_html = fr.content()
                fname = f'ors_phase3_frame_{i}.html'
                with open(fname, 'w') as f:
                    f.write(fr_html)
                log.info(f"[{label}] saved raw HTML ({len(fr_html)} chars) -> {fname}")
            except Exception as e:
                log.info(f"[{label}] frame.content() FAILED: {str(e)[:200]}")

        # ── PHASE 4 — Select CC070 (Ellis County Clerk) ─────────────────────
        # The search form's P_1 dropdown lists ~60 small-county LGS-client
        # offices, including "CC070=Ellis County Clerk" / "DC070=Ellis
        # District Clerk". Probate is a County Clerk matter in TX (per this
        # repo's README — County Court at Law hears estates), so select the
        # CC070 (County Clerk) option, not DC070.
        log.info("=" * 70)
        log.info("PHASE 4 — Select CC070 (Ellis County Clerk), inspect panels")
        log.info("=" * 70)

        search_frame = get_frame_by_name('update')
        if not search_frame:
            log.error("Could not find 'update' frame with the search form — aborting Phase 4.")
            browser.close()
            return

        try:
            search_frame.locator('select[name="P_1"]').select_option(value='CC070', timeout=10000)
            log.info("Selected P_1 = CC070 (Ellis County Clerk)")
        except Exception as e:
            log.error(f"Selecting P_1=CC070 FAILED: {str(e)[:300]}")
            browser.close()
            return

        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state('load', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        try:
            page.screenshot(path='ors_after_office_select.png', full_page=True)
            log.info("Saved screenshot -> ors_after_office_select.png")
        except Exception as e:
            log.info(f"screenshot FAILED: {str(e)[:200]}")

        search_frame2 = get_frame_by_name('update')
        if not search_frame2:
            log.error("'update' frame gone after office select — aborting.")
            browser.close()
            return

        try:
            panel_vis = search_frame2.evaluate("""() => {
                const ids = ['layer1','layer21','layer22','layer23','layer24','layer25','layer26'];
                return ids.map(id => {
                    const el = document.getElementById(id);
                    if (!el) return {id, present:false};
                    const cs = window.getComputedStyle(el);
                    return {id, present:true, visibility:cs.visibility, display:cs.display};
                });
            }""")
            log.info(f"Panel visibility after office select (layer25=Probate): {panel_vis}")
        except Exception as e:
            log.info(f"panel visibility check FAILED: {str(e)[:300]}")

        try:
            fr_html = search_frame2.content()
            with open('ors_after_office_select.html', 'w') as f:
                f.write(fr_html)
            log.info(f"Saved raw HTML ({len(fr_html)} chars) -> ors_after_office_select.html")
        except Exception as e:
            log.info(f"content() FAILED: {str(e)[:200]}")

        # ── PHASE 4b — Click the "Probate" category button ──────────────────
        # Selecting the office doesn't reveal layer25 directly. It relabels a
        # set of placeholder buttons (WTKCB_1.."L", WTKCB_2.."O", WTKCB_3..
        # "A" etc, literally spelling "LOADING" as placeholders) into the
        # category buttons actually offered by that office. For CC070 (Ellis
        # County Clerk) that diff showed exactly 3 categories appear: Criminal
        # / Civil / Probate (no Property/Vitals/Trustee for this office via
        # LGS -- Ellis uses a separate AcclaimWeb system for property
        # recording). Click by visible text, not by assuming a fixed WTKCB_N
        # index, since the mapping could differ per office.
        log.info("=" * 70)
        log.info("PHASE 4b — Click 'Probate' category button")
        log.info("=" * 70)

        try:
            probate_btn = search_frame2.locator('button:text-is("Probate")')
            log.info(f"'Probate' button count={probate_btn.count()}")
            probate_btn.click(timeout=10000)
            log.info("Clicked 'Probate' category button")
        except Exception as e:
            log.error(f"Click 'Probate' button FAILED: {str(e)[:300]}")
            browser.close()
            return

        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state('load', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        try:
            page.screenshot(path='ors_after_probate_click.png', full_page=True)
            log.info("Saved screenshot -> ors_after_probate_click.png")
        except Exception as e:
            log.info(f"screenshot FAILED: {str(e)[:200]}")

        search_frame3 = get_frame_by_name('update')
        if not search_frame3:
            log.error("'update' frame gone after Probate click — aborting.")
            browser.close()
            return

        try:
            panel_vis2 = search_frame3.evaluate("""() => {
                const ids = ['layer1','layer21','layer22','layer23','layer24','layer25','layer26'];
                return ids.map(id => {
                    const el = document.getElementById(id);
                    if (!el) return {id, present:false};
                    const cs = window.getComputedStyle(el);
                    return {id, present:true, visibility:cs.visibility, display:cs.display};
                });
            }""")
            log.info(f"Panel visibility after Probate click: {panel_vis2}")
        except Exception as e:
            log.info(f"panel visibility check FAILED: {str(e)[:300]}")

        try:
            fr_html = search_frame3.content()
            with open('ors_after_probate_click.html', 'w') as f:
                f.write(fr_html)
            log.info(f"Saved raw HTML ({len(fr_html)} chars) -> ors_after_probate_click.html")
        except Exception as e:
            log.info(f"content() FAILED: {str(e)[:200]}")

        # ── PHASE 5 — Probate Search: last 30 days, submit ──────────────────
        log.info("=" * 70)
        log.info("PHASE 5 — Probate Search (layer25): last 30 days, submit")
        log.info("=" * 70)

        # Use the freshest 'update' frame reference for all Phase 5 actions.
        search_frame2 = search_frame3

        end_d = date.today()
        start_d = end_d - timedelta(days=30)
        begin_str = start_d.strftime('%m/%d/%Y')
        end_str = end_d.strftime('%m/%d/%Y')
        log.info(f"Using date range {begin_str} - {end_str}")

        try:
            search_frame2.locator('input[name="P_38"]').fill(begin_str, timeout=8000)
            search_frame2.locator('input[name="P_190"]').fill(end_str, timeout=8000)
            log.info(f"Filled P_38(Beginning Date)={begin_str!r} P_190(Ending Date)={end_str!r}")
        except Exception as e:
            log.error(f"Filling probate date fields FAILED: {str(e)[:300]}")

        try:
            search_frame2.locator('select[name="P_56"]').select_option(value='D|ORPR_2', timeout=8000)
            log.info("Selected P_56 = D|ORPR_2 (File Date Descending)")
        except Exception as e:
            log.info(f"Selecting sort order FAILED (non-fatal): {str(e)[:200]}")

        try:
            search_frame2.locator('button[name="WTKCB_12"]').click(timeout=10000)
            log.info("Clicked WTKCB_12 (Probate Search submit)")
        except Exception as e:
            log.error(f"Click WTKCB_12 FAILED: {str(e)[:300]}")
            browser.close()
            return

        page.wait_for_timeout(7000)
        try:
            page.wait_for_load_state('load', timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        try:
            page.screenshot(path='ors_probate_results.png', full_page=True)
            log.info("Saved screenshot -> ors_probate_results.png")
        except Exception as e:
            log.info(f"screenshot FAILED: {str(e)[:200]}")

        frames5 = dump_frames(page)
        for i, fr in enumerate(frames5):
            label = f"phase5-frame[{i}]:{fr.name}:{fr.url}"
            st = check_structure(fr, label)
            if st.get('htmlLen', 999) < 60:
                continue
            safe_body_text(fr, label, limit=9000)
            try:
                fr_html = fr.content()
                fname = f'ors_probate_results_frame_{i}.html'
                with open(fname, 'w') as f:
                    f.write(fr_html)
                log.info(f"[{label}] saved raw HTML ({len(fr_html)} chars) -> {fname}")
            except Exception as e:
                log.info(f"[{label}] frame.content() FAILED: {str(e)[:200]}")

            try:
                tbl_info = fr.evaluate("""() => {
                    const tables = Array.from(document.querySelectorAll('table'));
                    return tables.map((t,i) => ({
                        idx: i, id: t.id||'', rows: t.rows.length,
                        firstRowText: t.rows.length ? Array.from(t.rows[0].cells).map(c=>c.textContent.trim().slice(0,40)) : [],
                        secondRowText: t.rows.length > 1 ? Array.from(t.rows[1].cells).map(c=>c.textContent.trim().slice(0,40)) : []
                    })).filter(t => t.rows > 1);
                }""")
                log.info(f"[{label}] tables with >1 row: {tbl_info}")
            except Exception as e:
                log.info(f"[{label}] table dump FAILED: {str(e)[:200]}")

        # ── PHASE 6 — Click "More Information" on row 1, dump case detail ──
        # The results frame's static template has a "Probate Pop-Up (Case
        # Detail)" panel (PANELTBL_22) with a "Representative Information"
        # grid (GRIDTBL_22B, fields P_109/P_110/P_111) that has NO visible
        # header labels in the static HTML -- headers are likely injected by
        # JS only once populated. This is our best lead for
        # executor/administrator name+address (the actionable contact), so
        # click into a real case and read the rendered grid.
        log.info("=" * 70)
        log.info("PHASE 6 — Click 'More Information' on first result row")
        log.info("=" * 70)

        results_frame = get_frame_by_name('update')
        if not results_frame:
            log.error("Could not find 'update' frame with results — aborting Phase 6.")
            browser.close()
            return

        try:
            more_info_btn = results_frame.locator('button:has-text("More Information"), a:has-text("More Information")').first
            log.info(f"'More Information' control count on page: "
                     f"{results_frame.locator(':text(\"More Information\")').count()}")
            more_info_btn.click(timeout=10000)
            log.info("Clicked first 'More Information' control")
        except Exception as e:
            log.error(f"Click 'More Information' FAILED: {str(e)[:300]}")
            browser.close()
            return

        page.wait_for_timeout(6000)
        try:
            page.wait_for_load_state('load', timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        try:
            page.screenshot(path='ors_case_detail.png', full_page=True)
            log.info("Saved screenshot -> ors_case_detail.png")
        except Exception as e:
            log.info(f"screenshot FAILED: {str(e)[:200]}")

        detail_frame = get_frame_by_name('update')
        if not detail_frame:
            log.error("'update' frame gone after More Information click — aborting.")
            browser.close()
            return

        safe_body_text(detail_frame, 'case-detail frame', limit=6000)

        try:
            fr_html = detail_frame.content()
            with open('ors_case_detail.html', 'w') as f:
                f.write(fr_html)
            log.info(f"Saved raw HTML ({len(fr_html)} chars) -> ors_case_detail.html")
        except Exception as e:
            log.info(f"content() FAILED: {str(e)[:200]}")

        # Dump the populated Case Information fields (P_97..P_108) by value,
        # and the Representative Information grid (P_109/P_110/P_111) with
        # whatever header text now exists, plus the raw <table> dump for
        # cross-check.
        try:
            case_fields = detail_frame.evaluate("""() => {
                const ids = ['P_97','P_98','P_99','P_100','P_101','P_102','P_103','P_104','P_105','P_106','P_107','P_108'];
                const out = {};
                for (const id of ids) {
                    const el = document.querySelector(`[name="${id}"]`);
                    out[id] = el ? el.value : null;
                }
                return out;
            }""")
            log.info(f"Case Information field values: {case_fields}")
        except Exception as e:
            log.info(f"case_fields dump FAILED: {str(e)[:300]}")

        try:
            rep_grid = detail_frame.evaluate("""() => {
                const tbl = document.getElementById('GRIDTBL_22B');
                if (!tbl) return null;
                return Array.from(tbl.rows).map(r => Array.from(r.cells).map(c => {
                    const inp = c.querySelector('input,textarea');
                    return inp ? inp.value : c.textContent.trim();
                }));
            }""")
            log.info(f"Representative Information grid (GRIDTBL_22B) rows: {rep_grid}")
        except Exception as e:
            log.info(f"rep_grid dump FAILED: {str(e)[:300]}")

        try:
            all_tables = detail_frame.evaluate("""() => {
                const tables = Array.from(document.querySelectorAll('table'));
                return tables.map((t,i) => ({
                    idx: i, id: t.id||'',
                    rows: Array.from(t.rows).slice(0,6).map(r => Array.from(r.cells).map(c => c.textContent.trim().slice(0,50)))
                })).filter(t => t.id.includes('22') || t.id.includes('GRID'));
            }""")
            log.info(f"All 22*/GRID tables (up to 6 rows each): {all_tables}")
        except Exception as e:
            log.info(f"all_tables dump FAILED: {str(e)[:300]}")

        dump_form(detail_frame, 'case-detail frame')

        browser.close()


if __name__ == '__main__':
    main()
