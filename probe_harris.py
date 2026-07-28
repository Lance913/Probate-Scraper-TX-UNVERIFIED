"""
Probe v1 -- Harris County Probate Court Search portal investigation.

Target assignment (SYSTEM_GUIDE.md Sec 6 investigation methodology):
  https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate
  https://www.cclerk.hctx.net/applications/websearch/CourtSettingsTyler.aspx?CaseType=Probate
  https://probate.harriscountytx.gov/  (official informational site -- corroborates
    which of the two cclerk.hctx.net paths is current/correct, and may itself list
    useful context/instructions)

This is a throwaway investigation script -- safe to delete once the portal is
understood and scrapers/harris.py is written. Do not guess form fields; dump
the real DOM.

Same domain as the working foreclosure scraper (Lance913/Scraper_Python,
scrapers/harris.py) -- reusing its anti-bot posture (webdriver flag override,
AutomationControlled disabled) and Playwright-first approach (that repo's
commit history shows plain requests-session cookies did NOT work reliably for
this domain -- Playwright was required), but this is a DIFFERENT application
path (CourtSearch.aspx, case index) so the form fields/table schema are
unknown and must be dumped fresh.
"""
import json
import logging
import os
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [probe] %(message)s')
log = logging.getLogger()

OUT_DIR = 'probe_out'
os.makedirs(OUT_DIR, exist_ok=True)

TARGETS = [
    ('courtsearch', 'https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate'),
    ('courtsettingstyler', 'https://www.cclerk.hctx.net/applications/websearch/CourtSettingsTyler.aspx?CaseType=Probate'),
    ('probate_info_site', 'https://probate.harriscountytx.gov/'),
]

FORM_DUMP_JS = """
() => {
  const out = {inputs: [], selects: [], buttons: [], forms: [], iframes: [], textareas: []};
  document.querySelectorAll('input').forEach(el => out.inputs.push({
    id: el.id, name: el.name, type: el.type, value: el.value, placeholder: el.placeholder
  }));
  document.querySelectorAll('select').forEach(el => out.selects.push({
    id: el.id, name: el.name,
    options: Array.from(el.options).map(o => ({value: o.value, text: o.text}))
  }));
  document.querySelectorAll('button').forEach(el => out.buttons.push({
    id: el.id, name: el.name, type: el.type, text: el.textContent.trim()
  }));
  document.querySelectorAll('input[type=submit],input[type=button]').forEach(el => out.buttons.push({
    id: el.id, name: el.name, type: el.type, text: el.value
  }));
  document.querySelectorAll('textarea').forEach(el => out.textareas.push({
    id: el.id, name: el.name
  }));
  document.querySelectorAll('form').forEach(el => out.forms.push({
    id: el.id, name: el.name, action: el.action, method: el.method
  }));
  document.querySelectorAll('iframe').forEach(el => out.iframes.push({
    id: el.id, name: el.name, src: el.src
  }));
  return out;
}
"""

LINKS_JS = """
() => Array.from(document.querySelectorAll('a')).map(a => ({
  text: (a.textContent||'').trim().slice(0,80), href: a.getAttribute('href')
})).filter(l => l.href)
"""

BODY_TEXT_JS = "() => document.body ? document.body.innerText.slice(0, 3000) : ''"


def dump_page(page, slug):
    log.info(f"=== {slug}: final URL = {page.url}")
    log.info(f"=== {slug}: title = {page.title()}")
    try:
        page.wait_for_load_state('networkidle', timeout=15000)
    except Exception as e:
        log.warning(f"{slug}: networkidle wait: {e}")
    page.wait_for_timeout(1500)

    html = page.content()
    with open(f'{OUT_DIR}/{slug}.html', 'w') as f:
        f.write(html)
    log.info(f"{slug}: saved HTML ({len(html)} bytes)")

    try:
        page.screenshot(path=f'{OUT_DIR}/{slug}.png', full_page=True)
        log.info(f"{slug}: saved screenshot")
    except Exception as e:
        log.warning(f"{slug}: screenshot failed: {e}")

    try:
        form_data = page.evaluate(FORM_DUMP_JS)
        with open(f'{OUT_DIR}/{slug}_form.json', 'w') as f:
            json.dump(form_data, f, indent=2)
        log.info(
            f"{slug}: inputs={len(form_data['inputs'])} selects={len(form_data['selects'])} "
            f"buttons={len(form_data['buttons'])} forms={len(form_data['forms'])} "
            f"iframes={len(form_data['iframes'])} textareas={len(form_data['textareas'])}"
        )
        for s in form_data['selects']:
            opts = [o['text'] for o in s['options'][:25]]
            log.info(f"  SELECT name={s['name']!r} id={s['id']!r} options={opts}")
        for i in form_data['inputs']:
            if i['type'] not in ('hidden',):
                log.info(f"  INPUT type={i['type']!r} name={i['name']!r} id={i['id']!r} "
                          f"placeholder={i['placeholder']!r} value={i['value']!r}")
        for b in form_data['buttons']:
            log.info(f"  BUTTON type={b['type']!r} name={b['name']!r} id={b['id']!r} text={b['text']!r}")
        for fr in form_data['iframes']:
            log.info(f"  IFRAME id={fr['id']!r} name={fr['name']!r} src={fr['src']!r}")
        for fm in form_data['forms']:
            log.info(f"  FORM id={fm['id']!r} action={fm['action']!r} method={fm['method']!r}")
    except Exception as e:
        log.warning(f"{slug}: form dump failed: {e}")

    try:
        body_text = page.evaluate(BODY_TEXT_JS)
        log.info(f"{slug}: body text (first 3000 chars):\n{body_text}")
    except Exception as e:
        log.warning(f"{slug}: body text dump failed: {e}")

    if slug == 'probate_info_site':
        try:
            links = page.evaluate(LINKS_JS)
            cclerk_links = [l for l in links if l['href'] and 'cclerk' in l['href'].lower()]
            case_links = [l for l in links if l['href'] and
                          ('case' in l['href'].lower() or 'search' in l['href'].lower())]
            log.info(f"probate_info_site: {len(links)} total links; {len(cclerk_links)} mention cclerk:")
            for l in cclerk_links:
                log.info(f"    CCLERK LINK: {l['text']!r} -> {l['href']}")
            log.info(f"probate_info_site: {len(case_links)} mention case/search:")
            for l in case_links[:30]:
                log.info(f"    CASE/SEARCH LINK: {l['text']!r} -> {l['href']}")
        except Exception as e:
            log.warning(f"probate_info_site: link scan failed: {e}")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled'],
        )
        ctx = browser.new_context(
            accept_downloads=True,
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        )
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page.set_default_timeout(30000)

        for slug, url in TARGETS:
            log.info(f"\n\n##### Navigating to {slug}: {url}")
            try:
                resp = page.goto(url, wait_until='domcontentloaded')
                log.info(f"{slug}: HTTP status = {resp.status if resp else 'N/A'}")
                dump_page(page, slug)
            except Exception as e:
                log.error(f"{slug}: navigation failed: {e}", exc_info=True)

        browser.close()
    log.info("Probe complete.")


if __name__ == '__main__':
    main()
