#!/usr/bin/env python3
"""Tests for graduation_scorecard.py — metric math, graduation assessment,
test-record filtering, and parser normalization."""
import sys
import graduation_scorecard as sc

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


def _t(pnl, closed_at='2026-06-01', strategy='Bull Put Spread', direction='bull',
       fee=0.0, net=None):
    return {'system': 's', 'symbol': 'SPY', 'strategy': strategy,
            'direction': direction, 'pnl': pnl, 'qty': 1, 'fee': fee,
            'net_pnl': pnl - fee if net is None else net,
            'closed_at': closed_at, 'reason': '', 'is_test': False}


def test_basic_metrics():
    trades = [_t(100, '2026-06-01'), _t(-50, '2026-06-02'),
              _t(80, '2026-06-03'),  _t(-30, '2026-06-04')]
    m = sc.compute_metrics(trades, starting_capital=2000)
    check(m['n'] == 4, "n == 4")
    check(m['total_pnl'] == 100.0, f"total P&L == 100 (got {m['total_pnl']})")
    check(m['win_rate'] == 0.5, "win rate 50%")
    check(m['expectancy'] == 25.0, f"expectancy $25 (got {m['expectancy']})")
    check(abs(m['profit_factor'] - (180/80)) < 1e-6, f"profit factor 2.25 (got {m['profit_factor']:.2f})")
    check(m['best'] == 100 and m['worst'] == -50, "best/worst")


def test_max_drawdown():
    # cum: +100, +50, +130, +30  -> peak 130 at t3, trough 30 -> DD 100
    trades = [_t(100, '2026-06-01'), _t(-50, '2026-06-02'),
              _t(80, '2026-06-03'),  _t(-100, '2026-06-04')]
    m = sc.compute_metrics(trades, starting_capital=2000)
    check(m['max_drawdown'] == 100.0, f"max drawdown $100 (got {m['max_drawdown']})")
    check(abs(m['max_dd_pct'] - 0.05) < 1e-6, f"max DD 5% of $2000 (got {m['max_dd_pct']})")


def test_profit_factor_no_losses():
    m = sc.compute_metrics([_t(10), _t(20)], 1000)
    check(m['profit_factor'] == float('inf'), "PF inf with no losses")


def test_assessment_gates():
    # insufficient sample
    small = sc.compute_metrics([_t(10)] * 5, 2000)
    a = sc.assess_graduation(small, {'min_sample': 30, 'min_expectancy': 0.0,
                                     'min_profit_factor': 1.3, 'max_dd_pct': 0.15})
    check(not a['ready'] and any('sample' in r for r in a['reasons']), "blocks on small sample")

    # enough trades, positive edge, good PF, low DD -> READY
    good = sc.compute_metrics([_t(60, f'2026-06-{i:02d}') for i in range(1, 31)] +
                              [_t(-20, f'2026-07-{i:02d}') for i in range(1, 6)], 2000)
    a2 = sc.assess_graduation(good, {'min_sample': 30, 'min_expectancy': 0.0,
                                     'min_profit_factor': 1.3, 'max_dd_pct': 0.15})
    check(a2['ready'], f"READY when all criteria met (reasons: {a2['reasons']})")

    # negative expectancy blocks even with big sample
    losing = sc.compute_metrics([_t(-5, f'2026-06-{i:02d}') for i in range(1, 32)], 2000)
    a3 = sc.assess_graduation(losing)
    check(not a3['ready'] and any('expectancy' in r for r in a3['reasons']),
          "blocks on negative expectancy")


def test_test_record_filter():
    check(sc._is_test_record('TEST-AUTO-001'), "TEST-AUTO-001 flagged test")
    check(sc._is_test_record('AAA'), "AAA flagged test")
    check(sc._is_test_record('reconciled-20260618'), "reconciled-* flagged test")
    check(sc._is_test_record('x', success=False), "success=False flagged test")
    check(not sc._is_test_record('a1b2c3d4-uuid-real'), "real UUID not flagged")


def test_empty_metrics():
    m = sc.compute_metrics([], 2000)
    check(m['n'] == 0 and m['total_pnl'] == 0.0, "empty -> n=0, pnl=0")
    a = sc.assess_graduation(m)
    check(not a['ready'], "empty -> not ready")


def test_commission_model():
    # 2-leg Tradier spread, qty 1: 0.35 * 2 legs * 2 (round trip) * 1 = $1.40
    check(abs(sc._trade_fee('tradier', 'Bull Put Spread', 1) - 1.40) < 1e-6,
          f"Tradier 2-leg fee $1.40 (got {sc._trade_fee('tradier','Bull Put Spread',1)})")
    # Iron Condor (4 legs) Tradier: 0.35 * 4 * 2 = $2.80
    check(abs(sc._trade_fee('tradier', 'Iron Condor', 1) - 2.80) < 1e-6, "Tradier IC fee $2.80")
    # IBKR 2-leg: 0.65 * 4 = $2.60 ; qty 2 -> $5.20
    check(abs(sc._trade_fee('guardrail', 'debit_put_spread', 2) - 5.20) < 1e-6, "IBKR 2-leg qty2 fee $5.20")
    # Alpaca tiny: 0.05 * 4 = $0.20
    check(abs(sc._trade_fee('openclaw', 'bull_call', 1) - 0.20) < 1e-6, "Alpaca 2-leg fee $0.20")


def test_net_vs_gross():
    # gross +$30 each, fee $1.40 -> net +$28.60 each
    trades = [_t(30, f'2026-06-{i:02d}', fee=1.40) for i in range(1, 4)]
    g = sc.compute_metrics(trades, 2000, 'pnl')
    n = sc.compute_metrics(trades, 2000, 'net_pnl')
    check(g['total_pnl'] == 90.0, "gross total $90")
    check(abs(n['total_pnl'] - 85.8) < 1e-6, f"net total $85.80 after fees (got {n['total_pnl']})")
    check(n['total_fees'] == 4.2, f"fees summed $4.20 (got {n['total_fees']})")
    # a thin +$1 gross trade goes NET-NEGATIVE after a $1.40 fee
    thin = sc.compute_metrics([_t(1, fee=1.40)], 2000, 'net_pnl')
    check(thin['total_pnl'] < 0, "thin +$1 gross -> net negative after commission")


def test_economics_and_fixed_cost_gate():
    # 12 net-profitable trades over ~6 months
    trades = [_t(50, f'2026-{m:02d}-15', fee=1.40) for m in range(1, 13)]
    n = sc.compute_metrics(trades, 12000, 'net_pnl')
    econ = sc.compute_economics(n['total_pnl'], trades, fixed_monthly=90,
                                real_capital=12000, target_drag=0.07)
    check(abs(econ['fixed_drag_pct'] - 0.09) < 1e-6, f"fixed drag 9% of $12k (got {econ['fixed_drag_pct']})")
    check(econ['min_viable_capital'] == round(90*12/0.07, 0), "min viable capital = annual fixed / 7%")
    # net run-rate ~ (12*48.6)/~11mo ≈ $53/mo < $90 -> does NOT cover fixed
    check(econ['covers_fixed'] is False, "small run-rate does not cover $90/mo fixed")

    a = sc.assess_graduation(n, econ=econ)
    check(not a['ready'] and any('fixed overhead' in r for r in a['reasons']),
          f"graduation blocked by fixed-cost coverage ({a['reasons']})")

    # high run-rate that DOES cover fixed + clears thresholds with enough sample
    big = [_t(200, f'2026-{(i%12)+1:02d}-{(i%27)+1:02d}', fee=1.40) for i in range(35)]
    bn = sc.compute_metrics(big, 12000, 'net_pnl')
    be = sc.compute_economics(bn['total_pnl'], big, 90, 12000, 0.07)
    ba = sc.assess_graduation(bn, econ=be)
    check(be['covers_fixed'] and ba['ready'], f"covers fixed + READY when edge is large enough ({ba['reasons']})")


if __name__ == '__main__':
    print("🧪 Graduation scorecard tests\n")
    test_basic_metrics()
    test_max_drawdown()
    test_profit_factor_no_losses()
    test_assessment_gates()
    test_test_record_filter()
    test_empty_metrics()
    test_commission_model()
    test_net_vs_gross()
    test_economics_and_fixed_cost_gate()
    print(f"\n{'─'*50}\n  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
