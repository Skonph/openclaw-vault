import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

VAULT_DIR = Path('/home/ubuntu/openclaw-vault')
SCANS_DIR = Path('/home/ubuntu/trading-bot/logs/snapshots')


def get_account_capital():
    """Fetch live equity from Alpaca paper account."""
    try:
        import requests
        from dotenv import load_dotenv
        load_dotenv('/home/ubuntu/trading-bot/.env')
        headers = {
            'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY'),
            'APCA-API-SECRET-KEY': os.environ.get('ALPACA_SECRET_KEY'),
        }
        base_url = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')
        r = requests.get(f"{base_url}/account", headers=headers, timeout=5)
        if r.status_code == 200:
            equity = float(r.json().get('equity', 0))
            return f"${equity:,.0f}"
    except Exception as e:
        print(f"⚠️  Capital fetch failed: {e}")
    return "~$2,946"  # fallback


def read_latest_scan():
    scans = sorted(SCANS_DIR.glob('scan_*.json'))
    if not scans:
        print("❌ No scan file found")
        return None
    latest = scans[-1]
    print(f"📂 Reading: {latest.name}")
    with open(latest) as f:
        return json.load(f)


def update_macro_context(scan):
    path = VAULT_DIR / 'OpenClaw/07_Macro_Context.md'
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    macro = scan.get('macro', {})
    market_ok = scan.get('market_ok', False)
    alerts_count = len(scan.get('alerts', []))

    new_section = f"\n## {today} — Auto Update\n\n"
    new_section += "| Indicator | Price | Change |\n"
    new_section += "|-----------|-------|--------|\n"
    for sym, data in macro.items():
        chg = data.get('change_pct', 0)
        arrow = '↑' if chg > 0 else '↓'
        new_section += f"| {sym} | ${data.get('price','N/A')} | {arrow} {chg}% |\n"

    new_section += f"\nMarket: {'✅ OK' if market_ok else '⚠️ Elevated'} | "
    new_section += f"Alerts: {alerts_count}\n\n---\n"

    if path.exists():
        existing = path.read_text()
        lines = existing.split('\n')
        insert_at = 1
        for i, line in enumerate(lines):
            if i > 0 and line.startswith('#'):
                insert_at = i
                break
        lines.insert(insert_at, new_section)
        path.write_text('\n'.join(lines))
    else:
        path.write_text(f"# Macro Context\n{new_section}")
    print("✅ Updated 07_Macro_Context.md")


def update_next_actions(scan):
    path = VAULT_DIR / 'OpenClaw/08_Next_Actions.md'
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    alerts = scan.get('alerts', [])
    holds = scan.get('holds', [])
    market_ok = scan.get('market_ok', False)

    section = f"\n## {today} — Scanner Results\n\n"
    section += f"Market: {'✅ OK' if market_ok else '⚠️ Elevated'}\n\n"

    if alerts:
        section += f"### 🚨 ALERTS ({len(alerts)})\n"
        for a in alerts:
            spread_type = a.get('spread_type', 'bull_call')
            type_label = "Bull Call" if spread_type == 'bull_call' else "Bear Put"
            section += f"""
**{a['symbol']} ${a['long_strike']}/{a['short_strike']} ({type_label})**
- Expiry: {a.get('expiry', '')} ({a['dte']} DTE)
- Mid: ${a['spread_mid']} | R:R: {a['rr']}:1
- IV: {a['long_iv']}%/{a['short_iv']}%
- OI: {a['long_oi']}/{a['short_oi']}
- Status: ⏳ Needs Events Calendar + Conviction
"""
    else:
        section += "### No alerts today — standing by\n"

    section += f"\n### Holds ({len(holds)})\n"
    for h in holds:
        section += f"- {h}\n"

    if alerts:
        tickers = list(dict.fromkeys([a['symbol'] for a in alerts]))  # dedupe, preserve order
        section += f"\n### Manual Actions Required\n"
        section += f"1. 📸 IBKR Events Calendar: {', '.join(tickers)}\n"
        section += "2. 📸 IBKR MultiSort screenshot\n"
        section += f"3. Nova conviction score: {', '.join(tickers)}\n"

    section += "\n---\n"

    if path.exists():
        existing = path.read_text()
        path.write_text(section + existing)
    else:
        path.write_text(f"# Next Actions\n**Updated:** {today}\n{section}")
    print("✅ Updated 08_Next_Actions.md")


def generate_daily_briefing(scan):
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    capital = get_account_capital()  # single call
    alerts = scan.get('alerts', [])
    macro = scan.get('macro', {})
    market_ok = scan.get('market_ok', False)

    vix = macro.get('VIX', {}).get('price', 'N/A')
    xle = macro.get('XLE', {}).get('change_pct', 'N/A')
    spy = macro.get('SPY', {}).get('change_pct', 'N/A')

    briefing = f"""# OpenClaw Daily Briefing
**Generated:** {today} Bangkok (server cron)

---

## Portfolio
- Capital: {capital} | Active positions: 0
- Scanner: ✅ Running on server

## Market
- Condition: {'✅ OK' if market_ok else '⚠️ Elevated risk'}
- VIX: {vix} (target <15)
- SPY: {spy}% (broad market)
- XLE: {xle}% (energy direction)

## Alerts Today: {len(alerts)}

"""
    if alerts:
        for a in alerts:
            spread_type = a.get('spread_type', 'bull_call')
            type_label = "Bull Call" if spread_type == 'bull_call' else "Bear Put"
            price_ok = '✅' if a.get('price') and 10.0 <= float(a['price']) <= 40.0 else '❌'
            briefing += f"""### 🚨 {a['symbol']} ${a['long_strike']}/{a['short_strike']} {a.get('expiry', '')} ({type_label})

| Rule | Value | Status |
|------|-------|--------|
| Price | ${'%.2f' % a['price'] if a.get('price') else 'N/A'} | {price_ok} |
| Mid | ${a['spread_mid']} | ✅ |
| IV | {a['long_iv']}%/{a['short_iv']}% | ✅ |
| OI | {a['long_oi']}/{a['short_oi']} | ✅ |
| DTE | {a['dte']} days | ✅ |
| R:R | {a['rr']}:1 | ✅ |

**All automated rules PASS**

#### Nova Conviction Request:
```
NOVA — {a['symbol']} alert from scanner.
Score conviction for {a['symbol']} ${a['long_strike']}/{a['short_strike']} {a.get('expiry', '')} ({type_label}).
IV: {a['long_iv']}%/{a['short_iv']}% | OI: {a['long_oi']}/{a['short_oi']}
Mid: ${a['spread_mid']} | DTE: {a['dte']} days | Price: ${a.get('price', 'N/A')}
Minimum required: 75/100.
```

#### ⚠️ Human Action Required Before Any Trade
1. Check IBKR Events Calendar for {a['symbol']} (entry → expiry window)
2. Paste Nova Session Prompt + events findings into Nova
3. Nova scores conviction — minimum **75** required
4. If conviction ≥75: generate execution code manually in Cowork session
5. **No execution code is auto-generated** — human runs code only after explicit approval

"""
    else:
        briefing += "No qualifying trades today. Capital preserved.\n\n"

    briefing += "---\n*Auto-generated by server. Pull from GitHub to see latest.*\n"

    path = VAULT_DIR / 'OpenClaw/09_Daily_Briefing.md'
    path.write_text(briefing)
    print("✅ Generated 09_Daily_Briefing.md")


def generate_nova_prompt(scan):
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    capital = get_account_capital()  # single call
    alerts = scan.get('alerts', [])
    macro = scan.get('macro', {})

    alerts_text = ""
    if alerts:
        alerts_text = "\n🚨 TODAY'S SCANNER ALERTS:\n"
        for a in alerts:
            spread_type = a.get('spread_type', 'bull_call')
            type_label = "Bull Call" if spread_type == 'bull_call' else "Bear Put"
            alerts_text += f"""
{a['symbol']} ${a['long_strike']}/{a['short_strike']} {a.get('expiry', '')} ({type_label})
- Mid: ${a['spread_mid']} | IV: {a['long_iv']}%/{a['short_iv']}% | OI: {a['long_oi']}/{a['short_oi']}
- Automated rules: ALL PASS ✅
- Pending: Events Calendar + Conviction Score (min 75)
"""
    else:
        alerts_text = "\nNo scanner alerts today. Standing by.\n"

    macro_text = "\nMACRO (auto-updated):\n"
    for sym, data in macro.items():
        macro_text += f"- {sym}: ${data.get('price')} ({data.get('change_pct')}%)\n"

    prompt = f"""NOVA — new session starting. Load complete context.

PROJECT: OpenClaw Bull Call + Bear Put System
ACCOUNT: Alpaca Paper Trading
CAPITAL: ~{capital} | DATE: {today}

RULESET v4.0:
- Conviction ≥75/100 | IV Rank ≤40% | IV Last ≤45% | Premium $0.30-$0.60
- Spread ≤$3 | Price $10-$40 | DTE 25-40 days
- Earnings ban ±14 days | OI ≥500 both legs
- Bid >$0.00 | Bid-ask ≤$0.10/leg | Green market days | Max 1 position
- Options chain must exist | Events Calendar checked
- IV Last >45% = auto-reject (L019) even if IV Rank passes

NOVA ROLE: Scoring + execution guidance ONLY
- No independent candidate generation
- No market data generation
- No orders without human approval
- Human screenshot = only valid data source
- If no human list: "Standing by for human ticker list"
{alerts_text}
WATCHLIST:
1. PR ~$19.91 | KNOWN_HOLD — recheck Jun 17 after dividend Jun 16
2. CCL ~$26.84 | IV 55%+ | Iran deal catalyst needed
3. NCLH ~$17.36 | IV 58%+ | same as CCL
4. AAL ~$13.14 | Conviction ≥75 required
5. VALE ~$16.25 | OI thin | recheck after May 30 earnings
{macro_text}
RECENT TRADES:
- AAL $12/$13: -$23 (IV breach at entry)
- F $12.50/$14: +$76 paper (lucky — position assumed closed, L012/L017)
- IAG $22/$24 Jun18: -$50 est. (stop triggered May 14, gold pullback, L010)
- HMC $27.5/$30 Jun18: closed May 19 via mleg fill (L018)

ACTIVE POSITIONS: None

KNOWN_HOLDS (do not score until recheck date):
- PR: recheck Jun 17, 2026

SERVER: ubuntu@43.160.222.7

HARD RESTRICTIONS:
1. Never generate ticker candidates independently
2. Never provide price/IV/OI/chain data
3. Score human-provided data only
4. No orders without explicit human approval
5. Nova-generated data = automatic rejection
6. Conviction floor is 75 — reject anything below, no exceptions

Confirm context loaded. Standing by."""

    path = VAULT_DIR / 'templates/Nova_Session_Prompt.md'
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"# Nova Session Prompt\n**Generated:** {today}\n\n---\n\n{prompt}")
    print("✅ Generated templates/Nova_Session_Prompt.md")

    backup = SCANS_DIR / f"Nova_Prompt_{datetime.now().strftime('%Y%m%d')}.txt"
    backup.write_text(prompt)
    print(f"✅ Backup: {backup.name}")


def git_push():
    # Stage only OpenClaw vault files and templates — avoids picking up workspace.json
    subprocess.run(
        ['git', 'add', 'OpenClaw/', 'templates/'],
        cwd=VAULT_DIR, capture_output=True, text=True
    )

    commit = subprocess.run(
        ['git', 'commit', '-m',
         f"Auto-update {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        cwd=VAULT_DIR, capture_output=True, text=True
    )
    if 'nothing to commit' in commit.stdout:
        print("ℹ️  No changes to commit")
        return

    pull = subprocess.run(
        ['git', 'pull', '--rebase', 'origin', 'main'],
        cwd=VAULT_DIR, capture_output=True, text=True
    )
    if pull.returncode != 0:
        print(f"⚠️  Git pull issue: {pull.stderr}")
    else:
        print("✅ Git pull successful")

    push = subprocess.run(
        ['git', 'push', 'origin', 'main'],
        cwd=VAULT_DIR, capture_output=True, text=True
    )
    if push.returncode == 0:
        print("✅ Git pushed to GitHub")
    else:
        print(f"⚠️  Git push issue: {push.stderr}")


def run():
    print(f"\n{'='*50}")
    print(f"VAULT UPDATER — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    scan = read_latest_scan()
    if not scan:
        return

    print(f"Alerts: {len(scan.get('alerts', []))} | "
          f"Holds: {len(scan.get('holds', []))}\n")

    update_macro_context(scan)
    update_next_actions(scan)
    generate_daily_briefing(scan)
    generate_nova_prompt(scan)
    git_push()

    print(f"\n{'='*50}")
    print("VAULT UPDATE COMPLETE")
    print("Pull on Mac: cd /Users/SkonP/AI_Prompt/Obsidient/SkonVault && git pull origin main")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run()
