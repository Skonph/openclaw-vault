# 📊 SKONVAULT LIVE GROUND TRUTH & OPERATING STATE
*Synchronized: 2026-09-28 22:31:06 ICT — source: LIVE broker + market data (no hardcoded prices)*

## 💼 Dual-Account Live Broker Balances
• Combined Broker Equity : $38,907.70
• Total Cash (live)      : $40,706.20 (225.6% of $18,042.37 graduation target)
• Real Cash Gap to Goal  : $0.00
• Buying Power (total)   : $127,711.05
• Deployed Max Risk      : $7,600.00 (19.5% of equity)
• alpaca_live: $30,497.36 cash | $29,918.36 equity | $109,189.44 buying power
• pion_main  : $4,701.72 cash | $4,578.72 equity | $12,806.88 buying power
• pion2_sub  : $3,335.87 cash | $2,409.87 equity | $4,143.48 buying power
• tradier_live: $2,171.25 cash | $2,000.75 equity | $1,571.25 buying power
• Fast-Track Progress     : n/a/10 closed | win rate n/a% | graduation target n/a

## 🌡️ Live Index Snapshot (Tradier/Finnhub/Alpaca)
• SPY $765.09 | QQQ $734.82 | IWM $279.08 | VIX $16.32

## 🌾 Active Defined Spreads (LIVE marks, live spots)
• NVDA $220.00P / $215.00P (6C | alpaca_live) — exp 2026-10-12 (14 DTE)
   ◦ Spot $230.18 (tradier) vs short strike → 4.42% OTM
   ◦ Credit $426.00 | Mark $546.00 | uPL $-120.00 | Max risk $3,000.00 | 50% TP $213.00
   ◦ 21-DTE exit deadline: 2026-09-21 | Flags: GAMMA_ZONE_14DTE
   ◦ STATUS: SAFE (4.42% OTM)
• XLE $60.00P / $58.00P (1C | alpaca_live) — exp 2026-10-16 (18 DTE)
   ◦ Spot $62.38 (tradier) vs short strike → 3.82% OTM
   ◦ Credit $36.00 | Mark $33.00 | uPL $3.00 | Max risk $200.00 | 50% TP $18.00
   ◦ 21-DTE exit deadline: 2026-09-25 | Flags: GAMMA_ZONE_18DTE
   ◦ STATUS: SAFE (3.82% OTM)
• AMD $590.00P / $585.00P (3C | pion2_sub) — exp 2026-10-16 (18 DTE)
   ◦ Spot $600.43 (tradier) vs short strike → 1.74% OTM
   ◦ Credit $450.00 | Mark $810.00 | uPL $-360.00 | Max risk $1,500.00 | 50% TP $225.00
   ◦ 21-DTE exit deadline: 2026-09-25 | Flags: GAMMA_ZONE_18DTE
   ◦ STATUS: WATCH — THIN BUFFER (1.74% OTM)
• IBIT $46.00P / $44.00P (4C | pion2_sub) — exp 2026-10-02 (4 DTE)
   ◦ Spot $46.99 (tradier) vs short strike → 2.11% OTM
   ◦ Credit $112.00 | Mark $116.00 | uPL $-4.00 | Max risk $800.00 | 50% TP $56.00
   ◦ 21-DTE exit deadline: 2026-09-11 | Flags: GAMMA_ZONE_4DTE
   ◦ STATUS: WATCH — THIN BUFFER (2.11% OTM)
• NVDA $215.00P / $210.00P (3C | pion_main) — exp 2026-10-09 (11 DTE)
   ◦ Spot $230.18 (tradier) vs short strike → 6.59% OTM
   ◦ Credit $228.00 | Mark $123.00 | uPL $105.00 | Max risk $1,500.00 | 50% TP $114.00
   ◦ 21-DTE exit deadline: 2026-09-18 | Flags: GAMMA_ZONE_11DTE
   ◦ STATUS: SAFE (6.59% OTM)
• TSM $425.00P / $420.00P (1C | tradier_live) — exp 2026-10-30 (32 DTE)
   ◦ Spot $446.5 (tradier) vs short strike → 4.82% OTM
   ◦ Credit $150.00 | Mark $0.00 | uPL $0.00 | Max risk $500.00 | 50% TP $75.00
   ◦ 21-DTE exit deadline: 2026-10-09 | Flags: none
   ◦ STATUS: SAFE (4.82% OTM)
• XLF $54.00P / $53.00P (1C | tradier_live) — exp 2026-10-16 (18 DTE)
   ◦ Spot $54.3601 (tradier) vs short strike → 0.66% OTM
   ◦ Credit $8.00 | Mark $0.00 | uPL $0.00 | Max risk $100.00 | 50% TP $4.00
   ◦ 21-DTE exit deadline: 2026-09-25 | Flags: GAMMA_ZONE_18DTE
   ◦ STATUS: WATCH — THIN BUFFER (0.66% OTM)

## 🛡️ Tail-Hedge Protection Legs
• AVGO $160P 2026-10-16 (14C, pion_main) — cost $84.00 | mark $0.00 | 18 DTE | spot $350.1367

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
