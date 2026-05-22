"""
run.py
======
EOD pipeline for the Momentum Breakout Agent.

Scheduling is handled by launchd (com.tradingagent.eod.plist), which fires
this script at 4:30 PM ET every weekday. The script runs once and exits.

Pipeline:
  1. scanner.py  — scan all tickers, write setups.json
  2. agent.py    — check exits, enter new trades, update positions.json
  3. tracker.py  — log closed trades to CSV, print daily summary
  4. alerts.py   — send Telegram messages for all events

Flags:
  --now   Skip the market-day check and run immediately (testing)
"""

import sys
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env', override=True)

try:
    import pandas_market_calendars as mcal
    _NYSE = mcal.get_calendar('NYSE')
    _CAL_AVAILABLE = True
except ImportError:
    _CAL_AVAILABLE = False

from scanner import run_scan
from agent   import run_agent
from tracker import run_tracker
from alerts  import run_alerts, send_telegram

EASTERN = pytz.timezone('US/Eastern')


def is_market_day(dt: datetime) -> bool:
    if not _CAL_AVAILABLE:
        return dt.weekday() < 5
    schedule = _NYSE.schedule(
        start_date=dt.strftime('%Y-%m-%d'),
        end_date=dt.strftime('%Y-%m-%d'),
    )
    return not schedule.empty


def run_pipeline(skip_market_check: bool = False):
    now_et = datetime.now(EASTERN)

    if not skip_market_check and not is_market_day(now_et):
        print(f"[Run] {now_et.strftime('%Y-%m-%d')} is not a market day — skipping")
        return

    print(f"\n{'='*62}")
    print(f"  MOMENTUM BREAKOUT AGENT — {now_et.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'='*62}")

    try:
        run_scan()
        positions, events = run_agent()
        run_tracker(positions)
        run_alerts(events)
        print(f"\n[Run] Pipeline complete — {now_et.strftime('%H:%M %Z')}")

    except Exception as exc:
        print(f"[Run] PIPELINE ERROR: {exc}")
        try:
            send_telegram(f"<b>EOD pipeline error</b>\n{exc}")
        except Exception:
            pass


if __name__ == '__main__':
    skip = '--now' in sys.argv
    if skip:
        print("[Run] --now flag: skipping market-day check")
    run_pipeline(skip_market_check=skip)
