"""
run.py
======
Master orchestrator for the Momentum Breakout Paper Trading Agent.

Scheduling modes
----------------
  Automatic (default):
      python run.py
      Runs at 4:30 PM ET every Mon–Fri using APScheduler.
      Skips NYSE holidays automatically via pandas_market_calendars.

  Immediate (testing / manual trigger):
      python run.py --now
      Runs the full pipeline once right now, skipping the market-day check.

Pipeline order on each run:
  1. scanner.py  — scan all 20 tickers, write setups.json
  2. agent.py    — check exits, enter new trades, update positions.json
  3. tracker.py  — log closed trades to CSV, print daily summary
  4. alerts.py   — send Telegram messages for all events
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env', override=True)

# ── Optional market-calendar check ───────────────────────────────────────────
try:
    import pandas_market_calendars as mcal
    _NYSE = mcal.get_calendar('NYSE')
    _CAL_AVAILABLE = True
except ImportError:
    _CAL_AVAILABLE = False
    print("[Run] pandas_market_calendars not installed — skipping holiday check")

# ── Optional scheduler ────────────────────────────────────────────────────────
try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    _SCHEDULER_AVAILABLE = True
except ImportError:
    _SCHEDULER_AVAILABLE = False
    print("[Run] APScheduler not installed — use 'python run.py --now' or a cron job instead")

# ── Agent modules ─────────────────────────────────────────────────────────────
from scanner import run_scan
from agent   import run_agent
from tracker import run_tracker
from alerts  import run_alerts, send_telegram

EASTERN = pytz.timezone('US/Eastern')

# ── Market day guard ──────────────────────────────────────────────────────────

def is_market_day(dt: datetime) -> bool:
    """Returns True if dt falls on an NYSE trading day."""
    if not _CAL_AVAILABLE:
        # Fallback: accept Mon–Fri and hope for no holidays
        return dt.weekday() < 5

    date_str = dt.strftime('%Y-%m-%d')
    schedule = _NYSE.schedule(start_date=date_str, end_date=date_str)
    return not schedule.empty

# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(skip_market_check: bool = False):
    """Run the full scanner → agent → tracker → alerts pipeline."""
    now_et = datetime.now(EASTERN)

    if not skip_market_check and not is_market_day(now_et):
        print(f"[Run] {now_et.strftime('%Y-%m-%d')} is not a market day — pipeline skipped")
        return

    print(f"\n{'='*62}")
    print(f"  MOMENTUM BREAKOUT AGENT — {now_et.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"{'='*62}")

    try:
        # Step 1 — scan for new setups
        run_scan()

        # Step 2 — manage exits and open new trades
        positions, events = run_agent()

        # Step 3 — log closed trades and print summary
        run_tracker(positions)

        # Step 4 — send Telegram alerts
        run_alerts(events)

        print(f"\n[Run] Pipeline complete — {now_et.strftime('%H:%M %Z')}")

    except Exception as exc:
        error_msg = f"[Run] PIPELINE ERROR: {exc}"
        print(error_msg)
        # Best-effort error alert
        try:
            send_telegram(f"<b>Pipeline error</b>\n{exc}")
        except Exception:
            pass

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # --now: run immediately, skip market-day check (useful for testing)
    if '--now' in sys.argv:
        print("[Run] --now flag: running pipeline immediately")
        run_pipeline(skip_market_check=True)
        return

    # Scheduled mode
    if not _SCHEDULER_AVAILABLE:
        print(
            "[Run] APScheduler is not installed.\n"
            "Install it with:  pip install APScheduler\n"
            "Or run manually:  python run.py --now"
        )
        sys.exit(1)

    print("[Run] Starting scheduler — pipeline will run at 4:30 PM ET on market days")
    print("[Run] Press Ctrl+C to stop\n")

    scheduler = BlockingScheduler(timezone=EASTERN)
    scheduler.add_job(
        run_pipeline,
        trigger=CronTrigger(
            day_of_week='mon-fri',   # Mon–Fri only (holiday check inside pipeline)
            hour=16,
            minute=30,
            timezone=EASTERN,
        ),
        id='momentum_pipeline',
        name='Momentum Breakout Daily Pipeline',
        misfire_grace_time=300,      # allow up to 5-min late start (e.g. after sleep)
        coalesce=True,               # if missed multiple, only run once
    )

    try:
        next_run = getattr(scheduler.get_jobs()[0], 'next_run_time', None)
        if next_run:
            print(f"[Run] Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M %Z')}\n")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n[Run] Scheduler stopped by user")


if __name__ == '__main__':
    main()
