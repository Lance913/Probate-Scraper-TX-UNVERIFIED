"""
Main orchestrator — Probate Scraper (9 TX counties)

Sister system to Lance913/Scraper_Python (the foreclosure system) and to
Foreclosure-Scraper-Collin-Ellis-Travis — same matrix+collate architecture,
a different data domain (decedent estate filings instead of foreclosure
notices), writing to the 'Probate Leads' + 'Probate Daily Counts' tabs of a
shared sheet.

Usage:
  python main.py                              # all counties, today
  python main.py --date 2026-06-25             # specific date
  python main.py --counties collin bexar       # specific counties
  python main.py --dry-run                     # scrape, don't write
  python main.py --counties collin --output records-collin.json   # matrix mode
  python main.py --from-json 'artifacts/**/records-*.json'        # collate mode
  python main.py --reset-sheet                 # wipe leads + tracker tabs
"""

import argparse
import glob
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime
from typing import List, Dict

from scrapers import (
    # Add each county's scraper class here as it's built, e.g.:
    # CollinCountyScraper,
    # BexarCountyScraper,
)
import sheets_writer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger('main')

# Add each county's slug here as its scraper is completed and merged.
ALL_COUNTIES = [
    # 'collin', 'dallas', 'ellis', 'bexar', 'tarrant', 'denton', 'johnson',
    # 'harris', 'travis',
]

SCRAPER_MAP = {
    # 'collin': CollinCountyScraper,
    # 'bexar':  BexarCountyScraper,
}


def parse_args():
    p = argparse.ArgumentParser(description='Probate Scraper — 9 TX counties')
    p.add_argument('--date', type=str, default=None,
                   help='Target date YYYY-MM-DD (default: today)')
    p.add_argument('--dry-run', action='store_true',
                   help='Scrape but do not write to Google Sheets')
    p.add_argument('--counties', nargs='+', choices=ALL_COUNTIES or None,
                   default=ALL_COUNTIES,
                   help='Counties to scrape (default: all)')
    p.add_argument('--output', type=str, default=None,
                   help='Write scraped records to this JSON file instead of Sheets '
                        '(used by the per-county matrix jobs).')
    p.add_argument('--from-json', nargs='+', default=None,
                   help='Glob(s) of record JSON files to load and write to Sheets '
                        '(used by the collate job). Skips scraping.')
    p.add_argument('--reset-sheet', action='store_true',
                   help='Wipe the leads tab and tracker tab (headers kept), then exit. '
                        'Use before a clean repopulating run.')
    return p.parse_args()


def _useful(r: Dict) -> bool:
    """Keep only records that identify a decedent. Unlike the foreclosure
    system, a probate case index is person-centric, not property-centric — a
    specific street address is frequently NOT part of the case index itself,
    so (unlike foreclosure) we do NOT require one here. Case number alone
    (with no decedent name at all) isn't actionable, so require the name."""
    return bool(r.get('decedent_last_name'))


def load_records(patterns: List[str]) -> List[Dict]:
    records: List[Dict] = []
    for pat in patterns:
        for path in sorted(glob.glob(pat, recursive=True)):
            try:
                with open(path) as f:
                    data = json.load(f)
                records.extend(data)
                logger.info(f"Loaded {len(data)} records from {path}")
            except Exception as exc:
                logger.error(f"Failed to read {path}: {exc}")
    return records


def run_scrapers(target_date: date, counties: List[str]) -> List[Dict]:
    all_records = []
    for name in counties:
        cls = SCRAPER_MAP[name]
        try:
            records = cls().scrape(target_date)
            logger.info(f"{name.title()}: {len(records)} records")
            all_records.extend(records)
        except Exception as exc:
            logger.error(f"{name.title()} scraper crashed: {exc}", exc_info=True)
    return all_records


def main():
    args = parse_args()

    if args.reset_sheet:
        logger.info("RESET — clearing leads tab and tracker tab.")
        sheets_writer.reset_all()
        logger.info("Reset complete. Run the scraper to repopulate.")
        return

    # Collate mode: load records from JSON artifacts and write them to Sheets.
    if args.from_json:
        records = [r for r in load_records(args.from_json) if _useful(r)]
        logger.info(f"Total records loaded: {len(records)}")
        if not records:
            logger.warning("No records to write.")
            return
        if args.dry_run:
            logger.info("DRY RUN — not writing to Sheets")
            for r in records:
                logger.info(r)
            return
        pull_date = (datetime.strptime(args.date, '%Y-%m-%d')
                     if args.date else datetime.now()).strftime('%m/%d/%Y')
        scanned_by_county = Counter(r.get('county', '') for r in records)
        new_records = sheets_writer.write_records(records, pull_date=pull_date)
        new_by_county = Counter(r.get('county', '') for r in new_records)
        sheets_writer.update_daily_tracker(pull_date, new_by_county)
        logger.info(f"Done. {len(new_records)} new rows added (pull date {pull_date}). "
                    f"New per county: {dict(new_by_county)} | "
                    f"scanned per county: {dict(scanned_by_county)}")
        return

    if not ALL_COUNTIES:
        logger.warning("No county scrapers wired in yet — nothing to do.")
        return

    target_date = (datetime.strptime(args.date, '%Y-%m-%d').date()
                   if args.date else date.today())

    logger.info(f"=== Probate Scraper (9 TX counties) | {target_date} ===")
    logger.info(f"Counties: {', '.join(args.counties)}")

    records = [r for r in run_scrapers(target_date, args.counties) if _useful(r)]
    logger.info(f"Total records collected: {len(records)}")

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(records, f)
        logger.info(f"Wrote {len(records)} records to {args.output}")
        return

    if not records:
        logger.warning("No records found.")
        return

    if args.dry_run:
        logger.info("DRY RUN — not writing to Sheets")
        for r in records:
            logger.info(r)
        return

    pull_date = target_date.strftime('%m/%d/%Y')
    new_records = sheets_writer.write_records(records, pull_date=pull_date)
    sheets_writer.update_daily_tracker(
        pull_date, Counter(r.get('county', '') for r in new_records))
    logger.info(f"Done. {len(new_records)} new rows added to Google Sheets.")


if __name__ == '__main__':
    main()
