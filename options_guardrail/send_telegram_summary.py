#!/usr/bin/env python3
"""Send Telegram deployment summary for marked-equity kill-switch."""
import json
import urllib.request

# Load credentials from openclaw/.env
env_path = "/home/ubuntu/openclaw/.env"
creds = {}
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()

BOT_TOKEN = creds["TELEGRAM_BOT_TOKEN"]
CHAT_ID = creds["TELEGRAM_CHAT_ID"]

MESSAGE = (
    "🛡️ *guardrail — marked-equity kill-switch deployed*\n\n"
    "*What changed*\n"
    "• `MARKED_EQUITY_KILLSWITCH` env var added (default: `true`)\n"
    "• All three risk profiles (CONSERVATIVE / MODERATE / AGGRESSIVE) now read this flag\n"
    "• When `true`: unrealized P&L of open positions is included in the day (−5%) and week (−10%) drawdown check that halts new entries\n"
    "• When `false`: reverts to realized-equity-only mode (previous behaviour)\n\n"
    "*Behaviour*\n"
    "Realized −3% + unrealized −2.5% = marked −5.5% → HALTED ✅\n"
    "Realized −3% alone = −3% → tradeable ✅\n"
    "Flag off: same unrealized ignored → tradeable ✅\n\n"
    "*New tests (4)*\n"
    "`test_marked_daily_unrealized_tips_over_halts`\n"
    "`test_marked_weekly_unrealized_tips_over_halts`\n"
    "`test_marked_killswitch_off_unrealized_ignored`\n"
    "`test_marked_near_threshold_not_halted`\n\n"
    "*deploy.sh result*\n"
    "3 failed (pre-existing ib\\_async mock isolation), 136 passed, 3 warnings — all 4 new marked-drawdown tests ✅\n\n"
    "Account: DUQ548647 | Options Level: 3 | Mode: paper"
)

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = json.dumps({
    "chat_id": CHAT_ID,
    "text": MESSAGE,
    "parse_mode": "Markdown",
}).encode()

req = urllib.request.Request(
    url, data=payload, headers={"Content-Type": "application/json"}
)
with urllib.request.urlopen(req) as resp:
    result = json.loads(resp.read())
    print("OK — message_id:", result["result"]["message_id"])
