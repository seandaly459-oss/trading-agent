"""
alerts.py
=========
Telegram notification sender for all trade events.

Events handled:
  entry       — new trade opened
  target1     — T1 hit, 50% sold, stop moved to breakeven
  stop_loss   — stop triggered, full position exited
  target2     — EMA exit on remaining 50% after T1

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as environment variables.
See README.md for instructions on creating a bot and getting your chat ID.
"""

import os
import requests
from datetime import datetime

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

# ── Core sender ───────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    """
    Send a plain-text or HTML message via Telegram bot.
    Returns True on success, False on any failure.
    """
    token   = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    if not token or not chat_id:
        print("[Alerts] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — message not sent")
        print(f"[Alerts] Message preview:\n{message}\n")
        return False

    url     = _TELEGRAM_URL.format(token=token)
    payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"[Alerts] Telegram send failed: {e}")
        return False

# ── Message formatters ────────────────────────────────────────────────────────

def _pct(new_val: float, base: float) -> str:
    return f"{((new_val / base) - 1) * 100:+.1f}%"


def _r_multiple(entry: float, stop: float, t1: float) -> str:
    risk   = entry - stop
    reward = t1 - entry
    if risk <= 0:
        return '?R'
    return f"{reward / risk:.1f}R"


def fmt_entry(e: dict) -> str:
    r_mult   = _r_multiple(e['entry'], e['stop'], e['t1'])
    vol_line = ""
    if e.get('volume') and e.get('vol_ma20'):
        vol_ratio = e['volume'] / e['vol_ma20']
        vol_line  = f"\n  • Volume {vol_ratio:.1f}× 20-day average ({e['volume']:,} vs avg {e['vol_ma20']:,})"
    rsi_line  = f"\n  • RSI {e['rsi']:.1f} (momentum zone 50–70)" if e.get('rsi') else ""
    ema_line  = f"\n  • Price above 20 EMA (${e['ema20']:.2f}) and 50 SMA" if e.get('ema20') else ""
    atr_line  = f"\n  • ATR ${e['atr']:.2f} — stop 1.5×, target 2× ATR" if e.get('atr') else ""
    return (
        f"<b>NEW TRADE — {e['ticker']}</b>\n"
        f"Entry   : ${e['entry']:.2f}\n"
        f"Stop    : ${e['stop']:.2f}  ({_pct(e['stop'], e['entry'])})\n"
        f"Target 1: ${e['t1']:.2f}  ({_pct(e['t1'], e['entry'])}, {r_mult})\n"
        f"Target 2: EMA-20 trailing exit\n"
        f"Shares  : {e['shares']}\n"
        f"Date    : {datetime.today().strftime('%Y-%m-%d')}\n"
        f"\n<b>Why we took this trade:</b>"
        f"\n  • 20-day resistance breakout confirmed"
        f"{vol_line}{rsi_line}{ema_line}{atr_line}"
        f"\n  • MACD crossed above signal line"
    )


def fmt_target1(e: dict) -> str:
    return (
        f"<b>TARGET 1 HIT — {e['ticker']}</b>\n"
        f"Entry   : ${e['entry']:.2f}\n"
        f"T1 exit : ${e['exit']:.2f}  ({_pct(e['exit'], e['entry'])})\n"
        f"P&amp;L : ${e['pnl']:+.2f}  (50% of position sold)\n"
        f"Stop    : moved to breakeven ${e['entry']:.2f}\n"
        f"Remaining 50% riding — exit on close below 20 EMA\n"
        f"\n<b>Why we sold half:</b>\n"
        f"  • Price hit 2× ATR profit target — locking in gains on 50%\n"
        f"  • Stop moved to breakeven — remaining 50% now risk-free\n"
        f"  • Letting the other half run for a larger EMA-trailing exit"
    )


def fmt_stop(e: dict) -> str:
    return (
        f"<b>STOP LOSS — {e['ticker']}</b>\n"
        f"Entry   : ${e['entry']:.2f}\n"
        f"Stop hit: ${e['exit']:.2f}  ({_pct(e['exit'], e['entry'])})\n"
        f"P&amp;L : ${e['pnl']:+.2f}\n"
        f"Full position exited"
    )


def fmt_target2(e: dict) -> str:
    return (
        f"<b>TARGET 2 — EMA EXIT — {e['ticker']}</b>\n"
        f"Entry   : ${e['entry']:.2f}\n"
        f"Exit    : ${e['exit']:.2f}  ({_pct(e['exit'], e['entry'])})\n"
        f"P&amp;L : ${e['pnl']:+.2f}  (remaining 50% closed)\n"
        f"Reason  : closed below 20 EMA"
    )

# ── Dispatcher ────────────────────────────────────────────────────────────────

_FORMATTERS = {
    'entry':    fmt_entry,
    'target1':  fmt_target1,
    'stop_loss': fmt_stop,
    'target2':  fmt_target2,
}


def run_alerts(events: list):
    """Process a list of trade event dicts and send each as a Telegram message."""
    if not events:
        print("[Alerts] No events to send")
        send_telegram(
            f"<b>Daily Scan Complete — {datetime.today().strftime('%Y-%m-%d')}</b>\n"
            f"No trades worth executing today."
        )
        return

    for event in events:
        event_type = event.get('type')
        formatter  = _FORMATTERS.get(event_type)

        if formatter is None:
            print(f"[Alerts] Unknown event type: {event_type!r} — skipping")
            continue

        message = formatter(event)
        ok      = send_telegram(message)
        status  = "sent" if ok else "failed (check env vars)"
        print(f"[Alerts] {event_type:<12} {event.get('ticker',''):<6}  {status}")


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("Sending test message to Telegram…")
    ok = send_telegram(
        "<b>Momentum Breakout Agent</b> is live!\n"
        f"Started: {datetime.today().strftime('%Y-%m-%d %H:%M')}"
    )
    print("Success" if ok else "Failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
