"""
scanner.py
==========
Daily EOD momentum breakout scanner.

Runs after market close, pulls data for all 20 tickers via yfinance,
evaluates all 5 entry conditions, calculates entry/stop/T1/EMA levels,
and writes valid setups to setups.json for the agent to act on.
"""

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

# ── Universe & parameters ─────────────────────────────────────────────────────

TICKERS = [
    'HOOD', 'SOFI', 'PLTR', 'ABNB', 'MSTR', 'NET',  'RBLX', 'SHOP', 'ARM',  'SNOW',
    'HIMS', 'RDDT', 'IONQ', 'AXON', 'DUOL', 'CELH', 'MELI', 'ASTS', 'AFRM', 'APP',
    'NVDA', 'TSLA', 'COIN', 'SMCI', 'META',
]

LOOKBACK          = 20     # days for resistance level and volume MA
RSI_PERIOD        = 14
ATR_PERIOD        = 14
EMA_FAST          = 20
SMA_SLOW          = 50
VOLUME_MULTIPLIER = 1.5
RSI_LOW, RSI_HIGH = 50, 70
STOP_MULT         = 1.5    # ATR multiples below entry
TARGET1_MULT      = 2.0    # ATR multiples above entry

SETUPS_FILE = os.path.join(os.path.dirname(__file__), 'setups.json')

# ── Technical indicators ──────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast    = close.ewm(span=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['ema20']      = df['Close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['sma50']      = df['Close'].rolling(SMA_SLOW).mean()
    df['rsi']        = compute_rsi(df['Close'], RSI_PERIOD)
    df['atr']        = compute_atr(df['High'], df['Low'], df['Close'], ATR_PERIOD)
    df['vol_ma20']   = df['Volume'].rolling(LOOKBACK).mean()
    df['resistance'] = df['High'].shift(1).rolling(LOOKBACK).max()   # prior 20-day high
    df['macd'], df['macd_signal'] = compute_macd(df['Close'])
    return df


def all_conditions_met(row: pd.Series) -> bool:
    """Returns True only when all 5 entry conditions are simultaneously satisfied."""
    try:
        return (
            row['Close']  > row['resistance']                            # 1. 20-day breakout
            and row['Volume'] >= VOLUME_MULTIPLIER * row['vol_ma20']     # 2. volume surge 1.5x
            and RSI_LOW <= row['rsi'] <= RSI_HIGH                        # 3. RSI 50–70
            and row['Close'] > row['ema20']                              # 4a. above 20 EMA
            and row['Close'] > row['sma50']                              # 4b. above 50 SMA
            and row['macd']  > row['macd_signal']                        # 5. MACD bullish
            and 5 <= row['Close'] <= 200                                 # price universe filter
            and row['vol_ma20'] >= 500_000                               # liquidity filter
        )
    except Exception:
        return False

# ── Main scan ─────────────────────────────────────────────────────────────────

def run_scan() -> list:
    scan_date = datetime.today().strftime('%Y-%m-%d')
    print(f"\n[Scanner] {scan_date} — scanning {len(TICKERS)} tickers for breakout setups")

    # Pull ~1 year so all indicators have enough warm-up history
    end   = datetime.today()
    start = end - timedelta(days=365)

    raw = yf.download(
        TICKERS,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        auto_adjust=True,
        progress=False,
    )

    setups = []

    for ticker in TICKERS:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw.xs(ticker, axis=1, level=1).dropna(how='all')
            else:
                df = raw.copy()

            if df.empty or len(df) < SMA_SLOW + 10:
                continue

            df     = add_indicators(df)
            latest = df.iloc[-1]

            if pd.isna(latest.get('resistance')) or pd.isna(latest.get('atr')):
                continue

            if not all_conditions_met(latest):
                continue

            close = float(latest['Close'])
            atr   = float(latest['atr'])
            ema20 = float(latest['ema20'])
            stop  = round(close - STOP_MULT   * atr, 4)
            t1    = round(close + TARGET1_MULT * atr, 4)

            setup = {
                'ticker':    ticker,
                'scan_date': scan_date,
                'price':     round(close, 4),
                'stop':      stop,
                't1':        t1,
                'ema20':     round(ema20, 4),
                'atr':       round(atr, 4),
                'rsi':       round(float(latest['rsi']), 2),
                'volume':    int(latest['Volume']),
                'vol_ma20':  int(latest['vol_ma20']),
            }
            setups.append(setup)
            print(
                f"  SETUP  {ticker:<6}  price={close:.2f}  "
                f"stop={stop:.2f}  T1={t1:.2f}  RSI={latest['rsi']:.1f}"
            )

        except Exception as e:
            print(f"  ERROR  {ticker}: {e}")

    output = {'scan_date': scan_date, 'setups': setups}
    with open(SETUPS_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n[Scanner] {len(setups)} setup(s) found — saved to setups.json")
    return setups


if __name__ == '__main__':
    run_scan()
