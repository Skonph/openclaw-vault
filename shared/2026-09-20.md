# 📊 SKONVAULT LIVE GROUND TRUTH & OPERATING STATE
*Synchronized: 2026-09-20 08:02:11 ICT — source: LIVE broker + market data (no hardcoded prices)*

## 💼 Dual-Account Live Broker Balances
• Combined Broker Equity : $7,982.37
• Total Cash (live)      : $10,524.37 (58.3% of $18,042.37 graduation target)
• Real Cash Gap to Goal  : $7,518.00
• Buying Power (total)   : $22,897.48
• Deployed Max Risk      : $4,800.00 (60.1% of equity)
• pion_main  : $7,561.07 cash | $5,339.07 equity | $22,244.28 buying power
• pion2_sub  : $2,963.30 cash | $2,643.30 equity | $653.20 buying power
• Fast-Track Progress     : 2/10 closed | win rate 100.0% | graduation target Sep 25, 2026

## 🌡️ Live Index Snapshot (Tradier/Finnhub/Alpaca)
• SPY $761.69 | QQQ $721.45 | IWM $284.10 | VIX $14.81

## 🌾 Active Defined Spreads (LIVE marks, live spots)
• AMD $490.00P / $485.00P (4C | pion2_sub) — exp 2026-10-02 (12 DTE)
   ◦ Spot $559.82 (tradier) vs short strike → 12.47% OTM
   ◦ Credit $220.00 | Mark $224.00 | uPL $-4.00 | Max risk $2,000.00 | 50% TP $110.00
   ◦ 21-DTE exit deadline: 2026-09-11 | Flags: GAMMA_ZONE_12DTE
   ◦ STATUS: SAFE (12.47% OTM)
• XLF $55.00P / $53.00P (4C | pion2_sub) — exp 2026-09-28 (8 DTE)
   ◦ Spot $55.86 (tradier) vs short strike → 1.54% OTM
   ◦ Credit $84.00 | Mark $96.00 | uPL $-12.00 | Max risk $800.00 | 50% TP $42.00
   ◦ 21-DTE exit deadline: 2026-09-07 | Flags: GAMMA_ZONE_8DTE
   ◦ STATUS: WATCH — THIN BUFFER (1.54% OTM)
• XLU $44.00P / $42.00P (10C | pion_main) — exp 2026-10-02 (12 DTE)
   ◦ Spot $41.1 (tradier) vs short strike → -7.06% OTM
   ◦ Credit $950.00 | Mark $2,250.00 | uPL $-1,300.00 | Max risk $2,000.00 | 50% TP $475.00
   ◦ 21-DTE exit deadline: 2026-09-11 | Flags: ITM_SHORT_LEG, GAMMA_ZONE_12DTE, LOSS_>=50%_OF_MAX_RISK
   ◦ STATUS: BREACH — SHORT $44P IS ITM (7.06% IN THE MONEY)

## 🛡️ Tail-Hedge Protection Legs
• AVGO $160P 2026-10-16 (14C, pion_main) — cost $84.00 | mark $0.00 | 26 DTE | spot $357.61
• NVDA $115P 2026-10-02 (14C, pion_main) — cost $98.00 | mark $28.00 | 12 DTE | spot $222.27

## ⚙️ Production Crontab (live)
• 07:15 ICT daily_report.py | 07:35 run_anna_dispatcher.py (Anna morning brief)
• 18:30 ICT Sun master_sunday_reset_daemon.py (Sunday pre-reset)
• 19:35 ICT dynamic_universe_screener.py (LIVE chain selection)
• 19:40 ICT dispatch_daily_premarket_brief.py (live SPY/VIX/positions)
• 21:10 & 21:45 ICT run_uoa_scan.py | 21:15 execute_golden_2115_daily_entry.py
• 21:55 ICT dispatch_peak_hour_brief.py | 20:30-03:30 auto_harvest_positions.py

## ⚠️ Standing Data-Integrity Rules
• Any position safety claim must show live spot + source + as-of timestamp.
• NEVER publish a hardcoded spot, strike or '100% SAFE' status.
• ITM short legs / 21-DTE gamma-zone flags override every 'coasting' narrative.
