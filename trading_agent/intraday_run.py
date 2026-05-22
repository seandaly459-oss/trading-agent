"""
intraday_run.py
===============
Morning status check for the Momentum Breakout Agent.

Scheduling is handled by launchd (com.tradingagent.intraday.plist), which
fires this script at 10:00 AM ET every weekday. Runs once and exits.

What it does:
  - Pulls live prices for all open positions from Alpaca
  - Calculates unrealised P&L and distance to stop / T1
  - Sends a Telegram morning brief summarising the portfolio
  - Skips silently on non-market days
"""

import json
import os
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

try:
    from alpaca.trading.client import TradingClient
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("[Intraday] WARNING: alpaca-py not installed")

from alerts import send_telegram

EASTERN        = pytz.timezone('US/Eastern')
_DIR           = os.path.dirname(__file__)
POSITIONS_FILE = os.path.join(_DIR, 'positions.json')


def _is_market_day() -> bool:
    today = datetime.now(EASTERN).strftime('%Y-%m-%d')
    if not _CAL_AVAILABLE:
        return datetime.now(EASTERN).weekday() < 5
    return not _NYSE.schedule(start_date=today, end_date=today).empty


def _get_client():
    api_key = os.environ.get('ALPACA_API_KEY')
    secret  = os.environ.get('ALPACA_SECRET_KEY')
    if not api_key or not secret:
        raise EnvironmentError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
    return TradingClient(api_key, secret, paper=True)


def _load_positions() -> dict:
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {}


def run_intraday():
    now_et = datetime.now(EASTERN)

    if not _is_market_day():
        print(f"[Intraday] {now_et.strftime('%Y-%m-%d')} is not a market day — skipping")
        return

    print(f"\n[Intraday] Morning check — {now_et.strftime('%Y-%m-%d %H:%M %Z')}")

    positions = _load_positions()
    open_pos  = {t: p for t, p in positions.items() if p.get('status') == 'open'}

    if not open_pos:
        send_telegram(
            f"<b>Morning Brief — {now_et.strftime('%Y-%m-%d')}</b>\n"
            f"No open positions. Watching for EOD setups."
        )
        print("[Intraday] No open positions")
        return

    if not ALPACA_AVAILABLE:
        print("[Intraday] alpaca-py not available — cannot fetch live prices")
        return

    try:
        client          = _get_client()
        alpaca_holdings = {p.symbol: p for p in client.get_all_positions()}
        equity          = float(client.get_account().equity)
    except Exception as e:
        print(f"[Intraday] Alpaca connection failed: {e}")
        return

    lines = [f"<b>Morning Brief — {now_et.strftime('%Y-%m-%d %H:%M %Z')}</b>"]
    lines.append(f"Account equity: ${equity:,.2f}\n")

    for ticker, pos in open_pos.items():
        entry  = pos['entry_price']
        stop   = pos['stop']
        t1     = pos['t1']
        shares = pos['shares_open']

        if ticker in alpaca_holdings:
            live   = float(alpaca_holdings[ticker].current_price)
            unreal = round((live - entry) * shares, 2)
            sign   = '+' if unreal >= 0 else ''
            pct    = round((live / entry - 1) * 100, 1)

            stop_dist = round(((live - stop) / live) * 100, 1)
            t1_dist   = round(((t1 - live)   / live) * 100, 1)
            t1_flag   = ' ✓T1' if pos.get('t1_hit') else ''

            lines.append(
                f"<b>{ticker}</b>{t1_flag}\n"
                f"  Live: ${live:.2f}  ({'+' if pct >= 0 else ''}{pct}%)\n"
                f"  P&amp;L: {sign}${unreal:.2f}  ·  {shares} shares\n"
                f"  Stop ${stop:.2f} ({stop_dist}% away)"
                + (f"  ·  T1 ${t1:.2f} ({t1_dist}% away)" if not pos.get('t1_hit') else "  ·  riding EMA trail")
            )
            print(f"[Intraday] {ticker:<6}  live={live:.2f}  P&L={sign}${unreal:.2f}")
        else:
            lines.append(f"<b>{ticker}</b> — not found in Alpaca (may be pending fill)")

    send_telegram("\n\n".join(lines))
    print("[Intraday] Morning brief sent")


if __name__ == '__main__':
    run_intraday()
