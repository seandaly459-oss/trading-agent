"""
tracker.py
==========
Trade logger and daily portfolio summary printer.

  - Reads positions.json for newly closed positions and appends them to trades_log.csv
  - Prints a formatted daily summary: open positions + running closed-trade stats
  - Can be imported by run.py or run standalone

CSV columns: ticker, entry_date, entry_price, stop, t1,
             exit_date, exit_price, exit_reason, shares, pnl, pnl_pct
"""

import csv
import json
import os
from datetime import datetime

_DIR           = os.path.dirname(__file__)
TRADES_LOG     = os.path.join(_DIR, 'trades_log.csv')
POSITIONS_FILE = os.path.join(_DIR, 'positions.json')

FIELDNAMES = [
    'ticker', 'entry_date', 'entry_price', 'stop', 't1',
    'exit_date', 'exit_price', 'exit_reason',
    'shares', 'pnl', 'pnl_pct',
]

# ── CSV helpers ───────────────────────────────────────────────────────────────

def _init_log():
    """Create trades_log.csv with header row if it doesn't exist."""
    if not os.path.exists(TRADES_LOG):
        with open(TRADES_LOG, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


def _already_logged(ticker: str, entry_date: str) -> bool:
    """Check if a (ticker, entry_date) pair already exists in the log."""
    if not os.path.exists(TRADES_LOG):
        return False
    with open(TRADES_LOG) as f:
        for row in csv.DictReader(f):
            if row['ticker'] == ticker and row['entry_date'] == str(entry_date):
                return True
    return False


def append_trade(pos: dict):
    """Write one closed position to trades_log.csv."""
    _init_log()

    entry    = float(pos.get('entry_price', 0))
    exit_p   = float(pos.get('exit_price',  0))
    shares   = int(pos.get('shares', 0))
    pnl      = round((exit_p - entry) * shares, 2)
    pnl_pct  = round((exit_p / entry - 1) * 100, 2) if entry else 0.0

    row = {
        'ticker':      pos.get('ticker', ''),
        'entry_date':  pos.get('entry_date', ''),
        'entry_price': entry,
        'stop':        pos.get('stop', ''),
        't1':          pos.get('t1', ''),
        'exit_date':   pos.get('exit_date', datetime.today().strftime('%Y-%m-%d')),
        'exit_price':  exit_p,
        'exit_reason': pos.get('exit_reason', ''),
        'shares':      shares,
        'pnl':         pnl,
        'pnl_pct':     pnl_pct,
    }
    with open(TRADES_LOG, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)

    sign = '+' if pnl >= 0 else ''
    print(f"[Tracker] Logged  {pos['ticker']:<6}  P&L=${sign}{pnl:.2f}  ({sign}{pnl_pct:.1f}%)  reason={pos.get('exit_reason')}")


def log_closed_positions(positions: dict):
    """Scan positions dict and log any newly closed positions not yet in CSV."""
    for ticker, pos in positions.items():
        if pos.get('status') != 'closed':
            continue
        if _already_logged(ticker, pos.get('entry_date', '')):
            continue
        append_trade(pos)

# ── Summary printer ───────────────────────────────────────────────────────────

def _load_trades() -> list:
    if not os.path.exists(TRADES_LOG):
        return []
    with open(TRADES_LOG) as f:
        return list(csv.DictReader(f))


def print_summary():
    """Print a formatted daily summary to stdout."""
    print('\n' + '=' * 62)
    print(f"  DAILY SUMMARY — {datetime.today().strftime('%Y-%m-%d %H:%M')}")
    print('=' * 62)

    # ── Open positions ────────────────────────────────────────────────────────
    positions = {}
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE) as f:
            positions = json.load(f)

    open_pos = {t: p for t, p in positions.items() if p.get('status') == 'open'}
    print(f"\nOPEN POSITIONS  ({len(open_pos)}/4)")

    if open_pos:
        header = f"  {'Ticker':<6}  {'Entry':>8}  {'Stop':>8}  {'T1':>8}  {'T1 Hit':>6}  {'Shares':>6}"
        print(header)
        print(f"  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*6}  {'─'*6}")
        for ticker, p in open_pos.items():
            t1_flag = 'YES' if p.get('t1_hit') else 'no'
            shares  = p.get('shares_open', p.get('shares', 0))
            print(
                f"  {ticker:<6}  ${p['entry_price']:>7.2f}"
                f"  ${p['stop']:>7.2f}  ${p['t1']:>7.2f}"
                f"  {t1_flag:>6}  {shares:>6}"
            )
    else:
        print("  None")

    # ── Closed trade stats ────────────────────────────────────────────────────
    trades = _load_trades()
    print(f"\nCLOSED TRADE STATS  ({len(trades)} trades)")

    if not trades:
        print("  No closed trades yet.")
    else:
        pnls     = [float(t['pnl']) for t in trades if t.get('pnl')]
        winners  = [p for p in pnls if p > 0]
        losers   = [p for p in pnls if p <= 0]
        total    = sum(pnls)
        win_rate = len(winners) / len(pnls) * 100 if pnls else 0
        avg_win  = sum(winners) / len(winners) if winners else 0
        avg_loss = sum(losers)  / len(losers)  if losers  else 0
        pf       = sum(winners) / abs(sum(losers)) if losers and sum(losers) != 0 else float('inf')

        print(f"  Total P&L      : ${total:+,.2f}")
        print(f"  Win rate       : {win_rate:.1f}%")
        print(f"  Avg winner     : ${avg_win:+.2f}")
        print(f"  Avg loser      : ${avg_loss:+.2f}")
        print(f"  Profit factor  : {pf:.2f}")

        # Per-exit-reason breakdown
        reasons: dict = {}
        for t in trades:
            r = t.get('exit_reason', 'unknown')
            reasons.setdefault(r, []).append(float(t['pnl']))
        print(f"\n  Exit breakdown:")
        for reason, pnl_list in sorted(reasons.items()):
            count   = len(pnl_list)
            avg     = sum(pnl_list) / count
            wins    = sum(1 for p in pnl_list if p > 0)
            print(f"    {reason:<22} count={count}  wins={wins}  avg=${avg:+.2f}")

    print('=' * 62 + '\n')


# ── Entry point ───────────────────────────────────────────────────────────────

def run_tracker(positions: dict = None):
    """Called by run.py after agent completes."""
    if positions is None:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE) as f:
                positions = json.load(f)
        else:
            positions = {}

    log_closed_positions(positions)
    print_summary()


if __name__ == '__main__':
    run_tracker()
