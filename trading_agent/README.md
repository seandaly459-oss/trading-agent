# Momentum Breakout Paper Trading Agent

Automated paper trading system for the finalized momentum breakout strategy.
Runs daily at 4:30 PM ET, scans 20 tickers, manages positions via Alpaca's
paper trading API, and sends Telegram alerts on every trade event.

---

## File Overview

```
trading_agent/
├── scanner.py        # EOD scanner — checks 5 entry conditions, writes setups.json
├── agent.py          # Position manager — entries/exits via Alpaca, updates positions.json
├── tracker.py        # Trade logger — appends closed trades to trades_log.csv, prints summary
├── alerts.py         # Telegram sender — notifies on entry, T1, stop, T2 exit
├── run.py            # Master orchestrator — runs pipeline, handles scheduling
├── requirements.txt  # Python dependencies
│
├── setups.json       # Auto-created: today's valid breakout setups
├── positions.json    # Auto-created: current open/closed position state
└── trades_log.csv    # Auto-created: permanent trade history
```

---

## Strategy Rules (Locked)

**Universe:** HOOD, SOFI, PLTR, ABNB, MSTR, NET, RBLX, SHOP, ARM, SNOW,
HIMS, RDDT, IONQ, AXON, DUOL, CELH, MELI, ASTS, AFRM, APP

**Entry — all 5 must be true:**
1. Close > highest high of the prior 20 days (breakout)
2. Volume ≥ 1.5× the 20-day average volume
3. RSI(14) between 50 and 70
4. Price above both the 20 EMA and 50 SMA
5. MACD line above signal line

**Exits:**
- Stop loss: 1.5× ATR(14) below entry price
- Target 1: sell 50% of position at 2× ATR above entry; stop moves to breakeven
- Target 2: exit remaining 50% when price closes below the 20 EMA
- No time stop

**Risk:** 2% of account equity per trade · max 4 open positions

---

## Quick Start

### 1. Install dependencies

```bash
cd trading_agent
pip install -r requirements.txt
```

### 2. Set up Alpaca paper trading account

1. Go to [alpaca.markets](https://alpaca.markets) and create a free account
2. In the dashboard, switch to **Paper Trading** (toggle at top right)
3. Go to **API Keys** → generate a new key pair for the paper account
4. Copy the API Key ID and Secret Key

### 3. Set up Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts — copy the **bot token** it gives you
3. Start a chat with your new bot (search for it by name and press Start)
4. Get your chat ID by visiting this URL in a browser (replace `YOUR_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
   Send your bot any message first, then open that URL. Look for `"chat":{"id":XXXXXXX}` — that number is your chat ID.

### 4. Set environment variables

**macOS / Linux:**
```bash
export ALPACA_API_KEY="your_alpaca_key_id"
export ALPACA_SECRET_KEY="your_alpaca_secret_key"
export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
export TELEGRAM_CHAT_ID="your_telegram_chat_id"
```

To make these permanent, add the four lines above to your `~/.zshrc` or `~/.bashrc`,
then run `source ~/.zshrc`.

**Windows (PowerShell):**
```powershell
$env:ALPACA_API_KEY="your_alpaca_key_id"
$env:ALPACA_SECRET_KEY="your_alpaca_secret_key"
$env:TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
$env:TELEGRAM_CHAT_ID="your_telegram_chat_id"
```

### 5. Test the Telegram connection

```bash
python alerts.py
```

You should receive a confirmation message in your Telegram chat.

### 6. Run the pipeline manually (first test)

```bash
python run.py --now
```

This skips the 4:30 PM schedule and runs the full pipeline immediately.
Check the output for any errors, and verify `setups.json` and `positions.json`
are created.

### 7. Start the scheduler (live use)

```bash
python run.py
```

The agent will wait until 4:30 PM ET and run automatically every market day.
Keep the terminal open, or run it in the background / as a service (see below).

---

## Running as a Background Service

### macOS — launchd (recommended)

Create `~/Library/LaunchAgents/com.momentumbot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.momentumbot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOUR_USERNAME/Documents/NewTrade/trading_agent/run.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ALPACA_API_KEY</key>       <string>your_key</string>
        <key>ALPACA_SECRET_KEY</key>    <string>your_secret</string>
        <key>TELEGRAM_BOT_TOKEN</key>   <string>your_token</string>
        <key>TELEGRAM_CHAT_ID</key>     <string>your_chat_id</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key>
    <string>/tmp/momentumbot.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/momentumbot_err.log</string>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.momentumbot.plist
```

### Linux — systemd

Create `/etc/systemd/system/momentumbot.service`:

```ini
[Unit]
Description=Momentum Breakout Paper Trading Agent
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/trading_agent/run.py
Restart=always
Environment="ALPACA_API_KEY=your_key"
Environment="ALPACA_SECRET_KEY=your_secret"
Environment="TELEGRAM_BOT_TOKEN=your_token"
Environment="TELEGRAM_CHAT_ID=your_chat_id"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable momentumbot
sudo systemctl start momentumbot
sudo systemctl status momentumbot
```

### Alternative — cron job

If you prefer cron over the built-in APScheduler:

```bash
# Edit crontab
crontab -e

# Add this line (runs at 4:30 PM ET — adjust if your server is not in ET)
30 16 * * 1-5 cd /path/to/trading_agent && python3 run.py --now >> /tmp/momentumbot.log 2>&1
```

---

## Daily Workflow

Each day at 4:30 PM ET the pipeline runs in this order:

```
scanner.py   →  setups.json        (which tickers triggered today)
agent.py     →  positions.json     (exits checked, new entries placed)
tracker.py   →  trades_log.csv     (closed trades logged, summary printed)
alerts.py    →  Telegram           (messages sent for every event)
```

**To check what happened today:**
```bash
python tracker.py
```

**To inspect open positions:**
```bash
cat positions.json
```

**To view trade history:**
```bash
cat trades_log.csv
```

---

## State Files

| File | Created by | Purpose |
|------|-----------|---------|
| `setups.json` | `scanner.py` | Today's valid breakout setups. Overwritten each run. |
| `positions.json` | `agent.py` | All positions (open and closed). Source of truth for strategy state. |
| `trades_log.csv` | `tracker.py` | Permanent append-only trade history. Never overwritten. |

---

## Modifying the Strategy

All strategy parameters are defined as constants at the top of each file.

| Parameter | File | Default |
|-----------|------|---------|
| Ticker list | `scanner.py` → `TICKERS` | 20 tickers |
| Lookback period | `scanner.py` → `LOOKBACK` | 20 days |
| RSI range | `scanner.py` → `RSI_LOW / RSI_HIGH` | 50–70 |
| Volume multiplier | `scanner.py` → `VOLUME_MULTIPLIER` | 1.5× |
| Stop loss multiplier | `scanner.py` → `STOP_MULT` | 1.5× ATR |
| Target 1 multiplier | `scanner.py` → `TARGET1_MULT` | 2.0× ATR |
| Risk per trade | `agent.py` → `RISK_PCT` | 0.02 (2%) |
| Max positions | `agent.py` → `MAX_POSITIONS` | 4 |
| Run time (ET) | `run.py` → `CronTrigger(hour=16, minute=30)` | 4:30 PM |

---

## Troubleshooting

**"ALPACA_API_KEY not set" error**
Environment variables aren't loaded. Run `export ALPACA_API_KEY=...` in the
same terminal session, or add them permanently to your shell profile.

**"alpaca-py not installed" warning**
Run `pip install alpaca-py`. Without it, the scanner still runs but no orders
are placed.

**Telegram messages not arriving**
- Confirm bot token and chat ID are correct
- Make sure you've sent the bot at least one message first (bots can't initiate)
- Test with `python alerts.py`

**No setups generated**
Normal — the 5-condition filter is strict. On quiet market days, zero setups
is expected. Check `setups.json` after a run to confirm the scanner executed.

**Positions out of sync with Alpaca**
The agent reconciles on every run — any position in `positions.json` marked
`open` that no longer exists in Alpaca will be marked `external_close`
automatically.

---

## Important Notes

- This is **paper trading only**. No real money is at risk.
- Orders are market orders placed after 4:30 PM ET — they fill at the **next
  day's opening price**, which will differ slightly from the signal price.
- The `entry_price` stored in `positions.json` is the theoretical EOD signal
  price. For precise fill tracking, check your Alpaca paper account dashboard.
- Past backtest performance (+133% over 2 years on v5 rules) does not guarantee
  future results. Run paper trading for at least 30–50 trades before considering
  any live account transition.
