"""
agent.py
========
Paper trading position manager.

On each run:
  1. Syncs local positions.json with actual Alpaca account state
  2. Checks every open position for stop loss, Target 1, and Target 2 exits
  3. Reads setups.json and enters new trades if capacity allows
  4. Saves updated state to positions.json
  5. Returns a list of events for alerts.py to process

All orders are market orders placed after EOD; they fill at the next day's open.
Stop and T1 levels are monitored in software rather than as Alpaca bracket orders,
which keeps the position state fully readable in positions.json at all times.
"""

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

# ── Alpaca import with clear error if missing ─────────────────────────────────
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("[Agent] WARNING: alpaca-py not installed — orders will be simulated only")

# ── Config ────────────────────────────────────────────────────────────────────

MAX_POSITIONS  = 4
RISK_PCT       = 0.02    # 2% of account equity per trade
MAX_POSITION_PCT = 0.40  # never risk more than 40% of account on one name
EMA_PERIOD     = 20

_DIR           = os.path.dirname(__file__)
SETUPS_FILE    = os.path.join(_DIR, 'setups.json')
POSITIONS_FILE = os.path.join(_DIR, 'positions.json')

# ── Alpaca client ─────────────────────────────────────────────────────────────

def get_client() -> 'TradingClient':
    api_key    = os.environ.get('ALPACA_API_KEY')
    secret_key = os.environ.get('ALPACA_SECRET_KEY')
    if not api_key or not secret_key:
        raise EnvironmentError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set as environment variables."
        )
    return TradingClient(api_key, secret_key, paper=True)


def get_account_equity(client) -> float:
    account = client.get_account()
    return float(account.equity)


def get_alpaca_positions(client) -> dict:
    """Returns {symbol: alpaca_position_object}."""
    return {p.symbol: p for p in client.get_all_positions()}

# ── Order helpers ─────────────────────────────────────────────────────────────

def market_buy(client, ticker: str, qty: int) -> str:
    """Place a market buy; returns Alpaca order ID."""
    order = client.submit_order(MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    ))
    return str(order.id)


def market_sell(client, ticker: str, qty: int) -> str:
    """Place a market sell for a specific qty; returns Alpaca order ID."""
    order = client.submit_order(MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    ))
    return str(order.id)

# ── Position state ────────────────────────────────────────────────────────────

def load_positions() -> dict:
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {}


def save_positions(positions: dict):
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(positions, f, indent=2, default=str)

# ── Live price data ───────────────────────────────────────────────────────────

def fetch_latest(tickers: list) -> dict:
    """
    Fetch recent EOD bars for a list of tickers.
    Returns {ticker: DataFrame with ema20 column}.
    """
    if not tickers:
        return {}

    end   = datetime.today()
    start = end - timedelta(days=90)   # 90 days is plenty for 20 EMA warm-up

    download_arg = tickers if len(tickers) > 1 else tickers[0]
    raw = yf.download(
        download_arg,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        auto_adjust=True,
        progress=False,
    )

    result = {}
    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna(how='all')
            else:
                df = raw.copy()

            if not df.empty:
                df['ema20'] = df['Close'].ewm(span=EMA_PERIOD, adjust=False).mean()
                result[ticker] = df
        except Exception:
            pass

    return result

# ── Main agent logic ──────────────────────────────────────────────────────────

def run_agent() -> tuple[dict, list]:
    """
    Execute one full cycle of position management and entry scanning.
    Returns (updated positions dict, list of alert events).
    """
    print(f"\n[Agent] {datetime.now().strftime('%Y-%m-%d %H:%M')} — running position manager")

    if not ALPACA_AVAILABLE:
        print("[Agent] alpaca-py unavailable — cannot place real orders")
        return load_positions(), []

    client   = get_client()
    equity   = get_account_equity(client)
    print(f"[Agent] Paper account equity: ${equity:,.2f}")

    positions        = load_positions()
    events: list     = []
    alpaca_positions = get_alpaca_positions(client)

    # ── 1. Reconcile with Alpaca ──────────────────────────────────────────────
    # If Alpaca no longer holds a position we think is open, mark it closed.
    for ticker, pos in list(positions.items()):
        if pos.get('status') == 'open' and ticker not in alpaca_positions:
            print(f"[Agent] {ticker} missing from Alpaca — marking closed (manual close or filled stop?)")
            pos['status']      = 'closed'
            pos['exit_date']   = datetime.today().strftime('%Y-%m-%d')
            pos['exit_reason'] = 'external_close'

    open_count = sum(
        1 for p in positions.values() if p.get('status') == 'open'
    )

    # ── 2. Check exits on every open position ─────────────────────────────────
    open_tickers = [t for t, p in positions.items() if p.get('status') == 'open']

    if open_tickers:
        price_data = fetch_latest(open_tickers)

        for ticker in open_tickers:
            pos = positions[ticker]

            if ticker not in price_data or price_data[ticker].empty:
                print(f"[Agent] No price data for {ticker} — skipping exit check")
                continue

            df     = price_data[ticker]
            latest = df.iloc[-1]
            close  = float(latest['Close'])
            ema20  = float(latest['ema20'])
            stop   = pos['stop']
            t1     = pos['t1']

            entry_price = pos['entry_price']
            shares      = pos['shares']
            shares_open = pos['shares_open']

            # --- Stop loss ---
            if close <= stop:
                fill = stop   # assume worst-case fill at the stop level
                print(f"[Agent] STOP LOSS  {ticker}  close={close:.2f} <= stop={stop:.2f}")
                if ticker in alpaca_positions:
                    market_sell(client, ticker, shares_open)
                pos.update({
                    'status':      'closed',
                    'exit_date':   datetime.today().strftime('%Y-%m-%d'),
                    'exit_price':  fill,
                    'exit_reason': 'stop_loss',
                })
                open_count -= 1
                events.append({
                    'type':   'stop_loss',
                    'ticker': ticker,
                    'entry':  entry_price,
                    'exit':   fill,
                    'stop':   stop,
                    't1':     t1,
                    'pnl':    round((fill - entry_price) * shares_open, 2),
                })
                continue

            # --- Target 1: sell 50% at 2x ATR above entry ---
            if not pos.get('t1_hit', False) and close >= t1:
                half = max(1, shares // 2)
                print(f"[Agent] TARGET 1   {ticker}  close={close:.2f} >= T1={t1:.2f}  selling {half} shares")
                if ticker in alpaca_positions:
                    market_sell(client, ticker, half)
                pos.update({
                    't1_hit':        True,
                    'shares_open':   shares_open - half,
                    't1_exit_price': close,
                    't1_exit_date':  datetime.today().strftime('%Y-%m-%d'),
                    'stop':          entry_price,   # trail stop to breakeven
                })
                events.append({
                    'type':   'target1',
                    'ticker': ticker,
                    'entry':  entry_price,
                    'exit':   close,
                    'stop':   stop,
                    't1':     t1,
                    'pnl':    round((close - entry_price) * half, 2),
                })
                # Fall through — also check T2 in case price reversed same day

            # --- Target 2: remaining 50% exits when close drops below 20 EMA ---
            if pos.get('t1_hit', False) and close < ema20:
                remaining = pos['shares_open']
                print(f"[Agent] TARGET 2   {ticker}  close={close:.2f} < EMA20={ema20:.2f}  selling {remaining} shares")
                if ticker in alpaca_positions:
                    market_sell(client, ticker, remaining)
                pos.update({
                    'status':      'closed',
                    'exit_date':   datetime.today().strftime('%Y-%m-%d'),
                    'exit_price':  close,
                    'exit_reason': 'target2_ema_exit',
                })
                open_count -= 1
                events.append({
                    'type':   'target2',
                    'ticker': ticker,
                    'entry':  entry_price,
                    'exit':   close,
                    'stop':   stop,
                    't1':     t1,
                    'pnl':    round((close - entry_price) * remaining, 2),
                })

    # ── 3. Enter new positions from today's setups ────────────────────────────
    if not os.path.exists(SETUPS_FILE):
        print("[Agent] No setups.json found — skipping new entries")
    else:
        with open(SETUPS_FILE) as f:
            scan = json.load(f)

        today = datetime.today().strftime('%Y-%m-%d')
        if scan.get('scan_date') != today:
            print(f"[Agent] setups.json is dated {scan.get('scan_date')} (not today) — skipping entries")
        else:
            for setup in scan.get('setups', []):
                if open_count >= MAX_POSITIONS:
                    print("[Agent] Max 4 positions reached — no more entries today")
                    break

                ticker = setup['ticker']

                # Skip if already holding this name
                if ticker in positions and positions[ticker].get('status') == 'open':
                    continue

                price          = setup['price']
                stop           = setup['stop']
                risk_per_share = price - stop

                if risk_per_share <= 0:
                    continue

                dollar_risk = equity * RISK_PCT
                shares      = int(dollar_risk / risk_per_share)
                cost        = shares * price

                # Safety checks
                if shares < 1:
                    print(f"[Agent] SKIP {ticker} — position size rounds to 0 shares")
                    continue
                if cost > equity * MAX_POSITION_PCT:
                    shares = int((equity * MAX_POSITION_PCT) / price)
                    cost   = shares * price
                    if shares < 1:
                        continue

                print(
                    f"[Agent] ENTRY     {ticker:<6}  {shares} shares @ ~{price:.2f}  "
                    f"stop={stop:.2f}  T1={setup['t1']:.2f}"
                )

                order_id = market_buy(client, ticker, shares)

                positions[ticker] = {
                    'ticker':         ticker,
                    'signal_date':    today,
                    'signal_price':   price,
                    'entry_order_id': order_id,
                    'entry_date':     today,
                    'entry_price':    price,     # theoretical; actual fill = next open
                    'shares':         shares,
                    'shares_open':    shares,
                    'stop':           stop,
                    't1':             setup['t1'],
                    'ema20':          setup['ema20'],
                    't1_hit':         False,
                    'status':         'open',
                }
                open_count += 1

                events.append({
                    'type':     'entry',
                    'ticker':   ticker,
                    'entry':    price,
                    'stop':     stop,
                    't1':       setup['t1'],
                    'shares':   shares,
                    'rsi':      setup.get('rsi'),
                    'atr':      setup.get('atr'),
                    'volume':   setup.get('volume'),
                    'vol_ma20': setup.get('vol_ma20'),
                    'ema20':    setup.get('ema20'),
                })

    save_positions(positions)
    print(f"[Agent] Done. Open positions: {open_count}/{MAX_POSITIONS}")

    return positions, events


if __name__ == '__main__':
    positions, events = run_agent()
    print(f"\nEvents generated: {len(events)}")
    for e in events:
        print(f"  {e['type']:15} {e['ticker']}")
