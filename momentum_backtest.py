"""
Momentum Breakout Strategy Backtester
======================================
Universe: mid-cap stocks with 500k+ avg daily volume, $5-$200 price range
Capital: $5,000 paper | Risk: 2% per trade | Max positions: 4
"""

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ── Configuration ─────────────────────────────────────────────────────────────

TICKERS = [
    'HOOD', 'SOFI', 'PLTR', 'ABNB', 'MSTR', 'NET',  'RBLX', 'SHOP', 'ARM',  'SNOW',
    'HIMS', 'RDDT', 'IONQ', 'AXON', 'DUOL', 'CELH', 'MELI', 'ASTS', 'AFRM', 'APP',
]
BENCHMARK = 'SPY'
INITIAL_CAPITAL = 5_000.0
RISK_PCT = 0.02          # 2% of capital at risk per trade
MAX_POSITIONS = 4
LOOKBACK = 20            # days for resistance / volume MA
RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_FAST = 20
SMA_SLOW = 50
VOLUME_MULTIPLIER = 1.5
RSI_LOW, RSI_HIGH = 50, 70
TARGET1_MULT = 2.0       # ATR multiples for T1
STOP_MULT = 1.5          # ATR multiples for stop loss
END_DATE = datetime.today()
START_DATE = END_DATE - timedelta(days=730)   # ~2 years

# ── Indicator Functions ───────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
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


def entry_signal(row) -> bool:
    """All five conditions must be true."""
    try:
        return (
            row['Close'] > row['resistance']                        # 1. breakout
            and row['Volume'] >= VOLUME_MULTIPLIER * row['vol_ma20']  # 2. volume surge
            and RSI_LOW <= row['rsi'] <= RSI_HIGH                   # 3. RSI zone
            and row['Close'] > row['ema20']                         # 4a. above 20 EMA
            and row['Close'] > row['sma50']                         # 4b. above 50 SMA
            and row['macd'] > row['macd_signal']                    # 5. MACD bullish
            and 5 <= row['Close'] <= 200                            # price filter
            and row['vol_ma20'] >= 500_000                          # volume filter
        )
    except Exception:
        return False

# ── Data Download ─────────────────────────────────────────────────────────────

def download_data(tickers: list, start: datetime, end: datetime) -> dict:
    print(f"Downloading data for {len(tickers)} tickers + SPY …")
    data = {}
    all_tickers = tickers + ([BENCHMARK] if BENCHMARK not in tickers else [])
    raw = yf.download(
        all_tickers,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        auto_adjust=True,
        progress=False
    )
    # yfinance returns multi-level columns when multiple tickers are requested
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in all_tickers:
            try:
                df = raw.xs(ticker, axis=1, level=1).dropna(how='all')
                if not df.empty:
                    data[ticker] = add_indicators(df)
            except KeyError:
                print(f"  Warning: no data for {ticker}")
    else:
        # single ticker case (shouldn't happen here but handle gracefully)
        data[all_tickers[0]] = add_indicators(raw.dropna(how='all'))
    print(f"  Downloaded {len(data)} instruments.\n")
    return data

# ── Position & Portfolio State ────────────────────────────────────────────────

class Position:
    def __init__(self, ticker, entry_date, entry_price, shares, stop, target1, atr):
        self.ticker       = ticker
        self.entry_date   = entry_date
        self.entry_price  = entry_price
        self.shares       = shares           # total shares at entry
        self.shares_open  = shares           # shares still open
        self.stop         = stop
        self.target1      = target1
        self.atr          = atr
        self.t1_hit       = False            # whether T1 was triggered
        self.trail_stop   = None             # activated after T1
        self.days_open    = 0
        self.realized_pnl = 0.0


class Portfolio:
    def __init__(self, capital: float):
        self.capital   = capital
        self.positions = {}          # ticker → Position
        self.trades    = []          # closed trade records
        self.equity    = []          # (date, equity) snapshots

    @property
    def open_count(self) -> int:
        return len(self.positions)

    def at_risk(self) -> float:
        """Current capital allocated (approximate mark-to-market not tracked intraday)."""
        return sum(p.shares_open * p.entry_price for p in self.positions.values())

    def unrealized(self, price_map: dict) -> float:
        total = 0.0
        for tkr, pos in self.positions.items():
            px = price_map.get(tkr, pos.entry_price)
            total += pos.shares_open * (px - pos.entry_price)
        return total

    def record_trade(self, ticker, entry_date, exit_date, entry_price,
                     exit_price, shares, exit_reason):
        pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price / entry_price - 1) * 100
        self.trades.append({
            'ticker':       ticker,
            'entry_date':   entry_date,
            'exit_date':    exit_date,
            'entry_price':  round(entry_price, 4),
            'exit_price':   round(exit_price, 4),
            'shares':       shares,
            'pnl':          round(pnl, 2),
            'pnl_pct':      round(pnl_pct, 2),
            'exit_reason':  exit_reason,
        })
        # Return full proceeds (cost basis was already deducted at entry)
        self.capital += exit_price * shares
        return pnl

# ── Backtest Engine ───────────────────────────────────────────────────────────

def run_backtest(data: dict, tickers: list) -> Portfolio:
    port = Portfolio(INITIAL_CAPITAL)

    # Build a common trading calendar from all tickers
    all_dates = sorted(set().union(*[set(data[t].index) for t in tickers if t in data]))

    for date in all_dates:
        price_map = {}
        for tkr in list(port.positions.keys()):
            if tkr in data and date in data[tkr].index:
                price_map[tkr] = data[tkr].loc[date, 'Close']

        # ── Manage existing positions ──────────────────────────────────────
        for tkr in list(port.positions.keys()):
            if tkr not in data or date not in data[tkr].index:
                continue

            pos   = port.positions[tkr]
            row   = data[tkr].loc[date]
            close = row['Close']
            ema20 = row['ema20']
            pos.days_open += 1

            # --- Stop loss (1.5x ATR, fixed at entry — no T1 breakeven move) ---
            if close <= pos.stop:
                port.record_trade(
                    tkr, pos.entry_date, date,
                    pos.entry_price, pos.stop, pos.shares_open, 'stop_loss'
                )
                del port.positions[tkr]
                continue

            # --- Full exit: close below 20 EMA (no T1, hold entire position) ---
            if close < ema20:
                port.record_trade(
                    tkr, pos.entry_date, date,
                    pos.entry_price, close, pos.shares_open, 'ema_exit'
                )
                del port.positions[tkr]
                continue


        # ── Scan for new entries ───────────────────────────────────────────
        if port.open_count < MAX_POSITIONS:
            for tkr in tickers:
                if port.open_count >= MAX_POSITIONS:
                    break
                if tkr in port.positions:
                    continue
                if tkr not in data or date not in data[tkr].index:
                    continue

                row = data[tkr].loc[date]
                if pd.isna(row['resistance']) or pd.isna(row['atr']):
                    continue

                if entry_signal(row):
                    close = row['Close']
                    atr   = row['atr']
                    stop  = close - STOP_MULT * atr
                    risk_per_share = close - stop
                    if risk_per_share <= 0:
                        continue

                    dollar_risk = port.capital * RISK_PCT
                    shares = int(dollar_risk / risk_per_share)
                    cost   = shares * close

                    # Don't enter if we can't afford even 1 share
                    if shares < 1 or cost > port.capital:
                        continue

                    target1 = close + TARGET1_MULT * atr
                    port.positions[tkr] = Position(
                        ticker=tkr,
                        entry_date=date,
                        entry_price=close,
                        shares=shares,
                        stop=stop,
                        target1=target1,
                        atr=atr
                    )
                    port.capital -= cost   # deduct cost basis

        # ── Daily equity snapshot ──────────────────────────────────────────
        equity = port.capital + sum(
            pos.shares_open * price_map.get(tkr, pos.entry_price)
            for tkr, pos in port.positions.items()
        )
        port.equity.append((date, equity))

    # Force-close any remaining open positions at last available price
    for tkr, pos in list(port.positions.items()):
        if tkr in data:
            last_price = data[tkr]['Close'].iloc[-1]
            port.record_trade(
                tkr, pos.entry_date, data[tkr].index[-1],
                pos.entry_price, last_price, pos.shares_open, 'end_of_backtest'
            )
    return port

# ── Performance Metrics ───────────────────────────────────────────────────────

def compute_metrics(port: Portfolio, spy_data: pd.DataFrame) -> dict:
    trades = pd.DataFrame(port.trades)
    equity_df = pd.DataFrame(port.equity, columns=['date', 'equity'])

    if trades.empty:
        return {'error': 'No trades generated.'}

    winners = trades[trades['pnl'] > 0]
    losers  = trades[trades['pnl'] <= 0]

    gross_profit = winners['pnl'].sum()
    gross_loss   = losers['pnl'].abs().sum()
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    equity_series = equity_df.set_index('date')['equity']
    peak          = equity_series.cummax()
    drawdown      = (equity_series - peak) / peak * 100
    max_dd        = drawdown.min()

    final_equity    = equity_series.iloc[-1]
    total_return    = (final_equity / INITIAL_CAPITAL - 1) * 100

    # SPY buy-and-hold
    spy_start = spy_data['Close'].iloc[0]
    spy_end   = spy_data['Close'].iloc[-1]
    spy_return = (spy_end / spy_start - 1) * 100

    best  = trades.loc[trades['pnl'].idxmax()]
    worst = trades.loc[trades['pnl'].idxmin()]

    return {
        'total_return_pct':     round(total_return, 2),
        'spy_return_pct':       round(spy_return, 2),
        'total_trades':         len(trades),
        'win_rate_pct':         round(len(winners) / len(trades) * 100, 2),
        'avg_winner_pct':       round(winners['pnl_pct'].mean(), 2) if not winners.empty else 0,
        'avg_loser_pct':        round(losers['pnl_pct'].mean(), 2) if not losers.empty else 0,
        'profit_factor':        round(profit_factor, 2),
        'max_drawdown_pct':     round(max_dd, 2),
        'final_equity':         round(final_equity, 2),
        'best_trade':           best.to_dict(),
        'worst_trade':          worst.to_dict(),
        'trades_df':            trades,
        'equity_df':            equity_df,
    }

# ── Output Functions ──────────────────────────────────────────────────────────

def save_equity_chart(equity_df: pd.DataFrame, spy_data: pd.DataFrame, metrics: dict):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8),
                                   gridspec_kw={'height_ratios': [3, 1]})
    fig.suptitle('Momentum Breakout Strategy v5b — Equity Curve (no Target 1, full EMA exit)', fontsize=14, fontweight='bold')

    eq = equity_df.set_index('date')['equity']
    ax1.plot(eq.index, eq.values, color='#2196F3', linewidth=1.5, label='Strategy')

    # Normalise SPY to same starting capital
    spy_close = spy_data['Close'].reindex(eq.index, method='ffill').dropna()
    spy_norm  = spy_close / spy_close.iloc[0] * INITIAL_CAPITAL
    ax1.plot(spy_norm.index, spy_norm.values, color='#FF9800',
             linewidth=1.2, linestyle='--', label='SPY B&H')

    ax1.axhline(INITIAL_CAPITAL, color='grey', linewidth=0.7, linestyle=':')
    ax1.set_ylabel('Portfolio Value ($)')
    ax1.legend()
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x:,.0f}'))
    ax1.grid(True, alpha=0.3)

    # Drawdown panel
    peak = eq.cummax()
    dd   = (eq - peak) / peak * 100
    ax2.fill_between(dd.index, dd.values, 0, color='#F44336', alpha=0.5, label='Drawdown')
    ax2.set_ylabel('Drawdown (%)')
    ax2.set_xlabel('Date')
    ax2.grid(True, alpha=0.3)

    # Annotate key stats
    stats_text = (
        f"Return: {metrics['total_return_pct']:+.1f}%  |  "
        f"SPY: {metrics['spy_return_pct']:+.1f}%  |  "
        f"Win Rate: {metrics['win_rate_pct']:.1f}%  |  "
        f"Max DD: {metrics['max_drawdown_pct']:.1f}%  |  "
        f"Profit Factor: {metrics['profit_factor']:.2f}"
    )
    fig.text(0.5, 0.01, stats_text, ha='center', fontsize=9, color='#333333')

    plt.tight_layout(rect=[0, 0.03, 1, 1])
    plt.savefig('backtest_equity_curve_v5b.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("Saved: backtest_equity_curve_v5b.png")


def save_trades_csv(trades_df: pd.DataFrame):
    trades_df.to_csv('backtest_trades_v5b.csv', index=False)
    print("Saved: backtest_trades_v5b.csv")


def save_summary(metrics: dict):
    best  = metrics['best_trade']
    worst = metrics['worst_trade']

    lines = [
        "=" * 60,
        "  MOMENTUM BREAKOUT STRATEGY — BACKTEST SUMMARY",
        f"  Period: {START_DATE.strftime('%Y-%m-%d')} → {END_DATE.strftime('%Y-%m-%d')}",
        f"  Tickers: {', '.join(TICKERS)}",
        "=" * 60,
        "",
        "RETURNS",
        f"  Strategy total return : {metrics['total_return_pct']:+.2f}%",
        f"  SPY buy-and-hold      : {metrics['spy_return_pct']:+.2f}%",
        f"  Alpha vs SPY          : {metrics['total_return_pct'] - metrics['spy_return_pct']:+.2f}%",
        f"  Final equity          : ${metrics['final_equity']:,.2f}",
        "",
        "TRADE STATISTICS",
        f"  Total trades          : {metrics['total_trades']}",
        f"  Win rate              : {metrics['win_rate_pct']:.1f}%",
        f"  Avg winner            : {metrics['avg_winner_pct']:+.2f}%",
        f"  Avg loser             : {metrics['avg_loser_pct']:+.2f}%",
        f"  Profit factor         : {metrics['profit_factor']:.2f}",
        f"  Max drawdown          : {metrics['max_drawdown_pct']:.2f}%",
        "",
        "BEST TRADE",
        f"  {best['ticker']}  {str(best['entry_date'])[:10]} → {str(best['exit_date'])[:10]}",
        f"  Entry ${best['entry_price']:.2f} → Exit ${best['exit_price']:.2f}",
        f"  P&L: ${best['pnl']:+.2f}  ({best['pnl_pct']:+.2f}%)  Reason: {best['exit_reason']}",
        "",
        "WORST TRADE",
        f"  {worst['ticker']}  {str(worst['entry_date'])[:10]} → {str(worst['exit_date'])[:10]}",
        f"  Entry ${worst['entry_price']:.2f} → Exit ${worst['exit_price']:.2f}",
        f"  P&L: ${worst['pnl']:+.2f}  ({worst['pnl_pct']:+.2f}%)  Reason: {worst['exit_reason']}",
        "",
        "EXIT REASON BREAKDOWN",
    ]

    trades_df = metrics['trades_df']
    reason_counts = trades_df.groupby('exit_reason').agg(
        count=('pnl', 'count'),
        avg_pnl=('pnl_pct', 'mean')
    )
    for reason, row in reason_counts.iterrows():
        lines.append(f"  {reason:<22} count={int(row['count'])}  avg={row['avg_pnl']:+.2f}%")

    # Per-ticker breakdown
    lines.append("")
    lines.append("PER-TICKER BREAKDOWN")
    ticker_stats = trades_df.groupby('ticker').agg(
        trades=('pnl', 'count'),
        wins=('pnl', lambda x: (x > 0).sum()),
        total_pnl=('pnl', 'sum'),
        avg_pnl_pct=('pnl_pct', 'mean'),
    )
    ticker_stats['win_rate'] = ticker_stats['wins'] / ticker_stats['trades'] * 100
    ticker_stats = ticker_stats.sort_values('total_pnl', ascending=False)
    total_pnl_all = ticker_stats['total_pnl'].sum()
    lines.append(f"  {'Ticker':<6}  {'Trades':>6}  {'WinRate':>7}  {'AvgP&L%':>8}  {'TotalP&L':>9}  {'Share%':>7}")
    lines.append(f"  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*8}  {'─'*9}  {'─'*7}")
    for tkr, r in ticker_stats.iterrows():
        share = r['total_pnl'] / total_pnl_all * 100 if total_pnl_all != 0 else 0
        lines.append(
            f"  {tkr:<6}  {int(r['trades']):>6}  {r['win_rate']:>6.1f}%  "
            f"{r['avg_pnl_pct']:>+7.2f}%  ${r['total_pnl']:>8.2f}  {share:>+6.1f}%"
        )

    lines += [
        "",
        "=" * 60,
        "Generated by momentum_backtest.py",
        "=" * 60,
    ]

    text = "\n".join(lines)
    with open('backtest_summary_v5b.txt', 'w') as f:
        f.write(text)
    print("Saved: backtest_summary_v5b.txt")
    return text


def analyse_edge(metrics: dict) -> str:
    """Simple rule-based commentary on strategy edge and weak points."""
    lines = []
    r = metrics['total_return_pct']
    spy = metrics['spy_return_pct']
    pf  = metrics['profit_factor']
    wr  = metrics['win_rate_pct']
    dd  = metrics['max_drawdown_pct']

    lines.append("\n── EDGE ANALYSIS ──────────────────────────────────────")
    if r > spy:
        lines.append(f"POSITIVE EDGE: strategy outperformed SPY by {r - spy:+.1f}pp.")
    else:
        lines.append(f"NO EDGE vs SPY: strategy underperformed by {spy - r:.1f}pp.")

    if pf > 1.5:
        lines.append(f"Profit factor {pf:.2f} is healthy (>1.5).")
    elif pf > 1.0:
        lines.append(f"Profit factor {pf:.2f} is marginally positive but fragile.")
    else:
        lines.append(f"Profit factor {pf:.2f} < 1.0 — strategy loses money in aggregate.")

    lines.append("\nWEAKEST RULE CANDIDATES:")

    # Win-rate analysis
    if wr < 40:
        lines.append("  • Win rate <40%: RSI 50-70 filter may be too wide, letting in "
                     "early breakouts that fail. Consider tightening to 55-65.")
    if wr > 65:
        lines.append("  • Win rate >65% but small profit factor suggests winners are cut "
                     "too early. Consider widening T1 to 3x ATR.")

    # Drawdown
    if dd < -20:
        lines.append(f"  • Max drawdown {dd:.1f}% is large. The 1.5x ATR stop may be too "
                     "loose on high-volatility names. Consider capping stop at 8% fixed.")

    # Exit reasons and concentration
    trades_df = metrics['trades_df']
    stop_losses = trades_df[trades_df['exit_reason'] == 'stop_loss']
    if len(stop_losses) > len(trades_df) * 0.4:
        lines.append("  • >40% of trades hit the initial stop. Volume filter (1.5x) may be "
                     "too permissive — many 'breakouts' are noise. Raise to 2.0x.")

    # Concentration risk: flag any ticker responsible for >40% of gross profit
    winners = trades_df[trades_df['pnl'] > 0]
    gross_profit = winners['pnl'].sum()
    if gross_profit > 0:
        by_ticker = winners.groupby('ticker')['pnl'].sum().sort_values(ascending=False)
        top_ticker = by_ticker.index[0]
        top_share = by_ticker.iloc[0] / gross_profit * 100
        if top_share > 40:
            lines.append(f"  • CONCENTRATION RISK: {top_ticker} accounts for {top_share:.0f}% of "
                         f"gross profit (${by_ticker.iloc[0]:,.0f}). Results are fragile — "
                         "a single missed trade changes the outcome significantly.")

    # Dead tickers: in universe but zero trades
    all_tickers_in_universe = set(TICKERS)
    traded_tickers = set(trades_df['ticker'].unique())
    silent = sorted(all_tickers_in_universe - traded_tickers)
    if silent:
        lines.append(f"  • No signals generated for: {', '.join(silent)}. "
                     "These names never met all 5 entry conditions over the 2-year window.")

    lines.append("─" * 54)
    return "\n".join(lines)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  MOMENTUM BREAKOUT BACKTESTER")
    print("=" * 60 + "\n")

    data = download_data(TICKERS, START_DATE, END_DATE)

    if BENCHMARK not in data:
        raise RuntimeError("Could not download SPY benchmark data.")

    spy_data = data[BENCHMARK]
    universe = [t for t in TICKERS if t in data]

    print(f"Running backtest on {len(universe)} tickers …")
    port = run_backtest(data, universe)

    if not port.trades:
        print("\nNo trades were generated. Check your data / filters.")
        return

    metrics = compute_metrics(port, spy_data)

    if 'error' in metrics:
        print(metrics['error'])
        return

    save_equity_chart(metrics['equity_df'], spy_data, metrics)
    save_trades_csv(metrics['trades_df'])
    summary_text = save_summary(metrics)

    print("\n" + summary_text)
    print(analyse_edge(metrics))


if __name__ == '__main__':
    main()
