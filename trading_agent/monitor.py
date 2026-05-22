"""
monitor.py
==========
Intraday position monitor for the Momentum Breakout Agent.

Called by run.py's scheduler every 30 minutes, 9:30 AM – 4:00 PM ET.
Gets live prices from Alpaca (current_price on open positions) and checks
each open position for stop loss, Target 1, and Target 2 EMA exit.
Fires market orders and Telegram alerts on any trigger, then updates
positions.json.

Not designed to be run directly — use: python run.py
For a one-shot manual check: python monitor.py
"""

import json
import os
from datetime import datetime, timedelta

import pandas as pd
import pytz
import yfinance as yf

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("[Monitor] WARNING: alpaca-py not installed — monitor cannot place orders")

from alerts import send_telegram

EASTERN       = pytz.timezone('US/Eastern')
EMA_PERIOD    = 20
MARKET_OPEN   = (9, 30)
MARKET_CLOSE  = (16, 0)

_DIR           = os.path.dirname(__file__)
POSITIONS_FILE = os.path.join(_DIR, 'positions.json')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _in_market_hours() -> bool:
    t = datetime.now(EASTERN)
    clock = (t.hour, t.minute)
    return MARKET_OPEN <= clock <= MARKET_CLOSE


def _get_client() -> 'TradingClient':
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


def _save_positions(positions: dict):
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(positions, f, indent=2, default=str)


def _fetch_ema20(tickers: list) -> dict:
    """Returns {ticker: current_ema20_value} using 90 days of daily closes."""
    if not tickers:
        return {}
    end   = datetime.today()
    start = end - timedelta(days=90)
    raw = yf.download(
        tickers if len(tickers) > 1 else tickers[0],
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        auto_adjust=True,
        progress=False,
    )
    result = {}
    for ticker in tickers:
        try:
            df = raw.xs(ticker, axis=1, level=1) if isinstance(raw.columns, pd.MultiIndex) else raw.copy()
            df = df.dropna(how='all')
            if not df.empty:
                result[ticker] = float(df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean().iloc[-1])
        except Exception:
            pass
    return result


def _market_sell(client, ticker: str, qty: int) -> str:
    order = client.submit_order(MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    ))
    return str(order.id)


def _pnl_str(pnl: float) -> str:
    return f"${pnl:+.2f}"


# ── Main monitor cycle ────────────────────────────────────────────────────────

def run_monitor():
    """
    One intraday check cycle. Called every 30 min by the scheduler in run.py.
    Guards against running outside market hours so it's safe to schedule broadly.
    """
    now_et = datetime.now(EASTERN)

    if not _in_market_hours():
        return

    print(f"\n[Monitor] {now_et.strftime('%Y-%m-%d %H:%M %Z')} — checking open positions")

    if not ALPACA_AVAILABLE:
        print("[Monitor] alpaca-py not available — skipping")
        return

    positions = _load_positions()
    open_pos  = {t: p for t, p in positions.items() if p.get('status') == 'open'}

    if not open_pos:
        print("[Monitor] No open positions")
        return

    try:
        client          = _get_client()
        alpaca_holdings = {p.symbol: p for p in client.get_all_positions()}
    except Exception as e:
        print(f"[Monitor] Alpaca connection failed: {e}")
        return

    ema20_map = _fetch_ema20(list(open_pos.keys()))
    changed   = False

    for ticker, pos in open_pos.items():
        if ticker not in alpaca_holdings:
            print(f"[Monitor] {ticker} not in Alpaca — skipping (may have been closed externally)")
            continue

        live_price  = float(alpaca_holdings[ticker].current_price)
        shares_open = pos['shares_open']
        entry_price = pos['entry_price']
        stop        = pos['stop']
        t1          = pos['t1']
        ema20       = ema20_map.get(ticker, pos.get('ema20', 0))

        print(
            f"[Monitor] {ticker:<6}  live={live_price:.2f}"
            f"  stop={stop:.2f}  T1={t1:.2f}  EMA20={ema20:.2f}"
            f"  shares={shares_open}"
        )

        # ── Stop loss ─────────────────────────────────────────────────────────
        if live_price <= stop:
            print(f"[Monitor] STOP HIT  {ticker}  {live_price:.2f} <= {stop:.2f}")
            try:
                _market_sell(client, ticker, shares_open)
            except Exception as e:
                print(f"[Monitor] Sell order failed for {ticker}: {e}")
                continue

            pnl = round((live_price - entry_price) * shares_open, 2)
            pos.update({
                'status':      'closed',
                'exit_date':   now_et.strftime('%Y-%m-%d'),
                'exit_price':  live_price,
                'exit_reason': 'stop_loss',
                'shares_open': 0,
            })
            changed = True

            send_telegram(
                f"<b>STOP HIT — {ticker}</b>\n"
                f"Sold {shares_open} shares @ ${live_price:.2f}\n"
                f"P&amp;L: {_pnl_str(pnl)}\n"
                f"Entry: ${entry_price:.2f}"
            )
            continue   # position closed, nothing else to check

        # ── Target 1: sell 50% at 2× ATR, move stop to breakeven ─────────────
        if not pos.get('t1_hit', False) and live_price >= t1:
            half = max(1, pos['shares'] // 2)
            print(f"[Monitor] T1 HIT    {ticker}  {live_price:.2f} >= {t1:.2f}  selling {half} shares")
            try:
                _market_sell(client, ticker, half)
            except Exception as e:
                print(f"[Monitor] T1 sell failed for {ticker}: {e}")
                continue

            pnl = round((live_price - entry_price) * half, 2)
            pos.update({
                't1_hit':        True,
                'shares_open':   shares_open - half,
                't1_exit_price': live_price,
                't1_exit_date':  now_et.strftime('%Y-%m-%d'),
                'stop':          entry_price,   # breakeven stop
            })
            changed = True

            send_telegram(
                f"<b>TARGET 1 HIT — {ticker}</b>\n"
                f"Sold 50% ({half} shares) @ ${live_price:.2f}\n"
                f"P&amp;L on half: {_pnl_str(pnl)}\n"
                f"Stop moved to breakeven (${entry_price:.2f})\n"
                f"Remaining {pos['shares_open']} shares still open"
            )
            # Fall through: also check T2 this cycle in case price reversed

        # ── Target 2: EMA exit on remaining 50% after T1 ─────────────────────
        if pos.get('t1_hit', False) and ema20 and live_price < ema20:
            remaining = pos['shares_open']
            if remaining < 1:
                continue
            print(f"[Monitor] T2 EMA    {ticker}  {live_price:.2f} < EMA20={ema20:.2f}  selling {remaining} shares")
            try:
                _market_sell(client, ticker, remaining)
            except Exception as e:
                print(f"[Monitor] T2 sell failed for {ticker}: {e}")
                continue

            pnl = round((live_price - entry_price) * remaining, 2)
            pos.update({
                'status':      'closed',
                'exit_date':   now_et.strftime('%Y-%m-%d'),
                'exit_price':  live_price,
                'exit_reason': 'target2_ema_exit',
                'shares_open': 0,
            })
            changed = True

            send_telegram(
                f"<b>TARGET 2 EXIT — {ticker}</b>\n"
                f"Price fell below 20 EMA (${ema20:.2f})\n"
                f"Sold remaining {remaining} shares @ ${live_price:.2f}\n"
                f"P&amp;L on remainder: {_pnl_str(pnl)}"
            )

    if changed:
        positions.update(open_pos)
        _save_positions(positions)
        print("[Monitor] positions.json updated")

    print(f"[Monitor] Cycle complete — {now_et.strftime('%H:%M %Z')}")


if __name__ == '__main__':
    run_monitor()
