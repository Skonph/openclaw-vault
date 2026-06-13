- Net credit = short put bid − long put ask
- Reject if net credit < $0.30 absolute minimum
- Reject if net credit < 15% of spread width (e.g. $0.30 minimum on a $2-wide)
- Reject if max loss > $200
- Score = net credit ÷ max loss (highest reward-per-dollar-risked wins)



Here's the complete decision tree now live in the system:

| SPY daily change | VIX      | Strategy                                 |
| ---------------- | -------- | ---------------------------------------- |
| Any              | > 30     | 🔴 Cash only                             |
| Any              | < 15     | ⚪ Pass — IV too cheap                    |
| −0.5% to +0.5%   | **≥ 18** | 🔵 **Iron Condor**                       |
| > +0.5%          | 15–30    | 🟢 Bull Put Spread                       |
| < −0.5%          | 15–30    | 🟡 Bear Call Spread                      |
| −0.5% to +0.5%   | 15–18    | 🟢 Bull Put Spread (single side default) |
  
  
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0f1117;
    color: #e8eaf0;
    padding: 24px 16px;
    min-height: 100vh;
  }
  h1 { text-align: center; font-size: 1.5rem; font-weight: 800; margin-bottom: 4px; color: #fff; }
  .subtitle { text-align: center; font-size: 0.85rem; color: #9aa0b4; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; max-width: 880px; margin: 0 auto; }
  .card { border-radius: 16px; padding: 20px; }
  .card.long { background: linear-gradient(135deg, #0d2a1f, #0a1f30); border: 1.5px solid #1db96450; }
  .card.short { background: linear-gradient(135deg, #2a0d0d, #1a0a2a); border: 1.5px solid #e53e3e50; }
  .card-title { font-size: 1.05rem; font-weight: 800; margin-bottom: 2px; display: flex; align-items: center; gap: 8px; }
  .badge { font-size: 0.68rem; padding: 2px 10px; border-radius: 20px; font-weight: 700; text-transform: uppercase; }
  .badge.buy { background: #1db964; color: #000; }
  .badge.sell { background: #e53e3e; color: #fff; }
  .card-role { font-size: 0.75rem; color: #9aa0b4; margin-bottom: 14px; }
  .analogy-box { border-radius: 10px; padding: 10px 12px; margin-bottom: 14px; font-size: 0.8rem; line-height: 1.5; }
  .long .analogy-box { background: #122d1f; border-left: 3px solid #1db964; }
  .short .analogy-box { background: #2d1212; border-left: 3px solid #e53e3e; }
  .stat-row { display: flex; justify-content: space-between; align-items: center; padding: 7px 0; border-bottom: 1px solid #ffffff0f; font-size: 0.78rem; }
  .stat-label { color: #9aa0b4; }
  .stat-val { font-weight: 700; }
  .green { color: #1db964; }
  .red { color: #e53e3e; }
  .yellow { color: #f6c90e; }
  .blue { color: #56b6f7; }
  .chart-wrap { margin-top: 14px; }
  .chart-label { font-size: 0.7rem; color: #9aa0b4; text-align: center; margin-bottom: 6px; }
  svg.pnl { display: block; width: 100%; height: 110px; }
  .summary-row { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; max-width: 880px; margin: 18px auto 0; }
  .summary-card { background: #161924; border-radius: 12px; padding: 14px 18px; border: 1px solid #2a2f3e; }
  .summary-card h3 { font-size: 0.78rem; color: #9aa0b4; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; }
  .step { display: flex; gap: 10px; margin-bottom: 8px; font-size: 0.76rem; line-height: 1.4; align-items: flex-start; }
  .step-num { min-width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.65rem; font-weight: 800; margin-top: 1px; }
  .step-num.g { background: #1db964; color: #000; }
  .step-num.r { background: #e53e3e; color: #fff; }
  .terms-section { max-width: 880px; margin: 18px auto 0; background: #161924; border-radius: 12px; padding: 16px 18px; border: 1px solid #2a2f3e; }
  .terms-section h3 { font-size: 0.78rem; color: #9aa0b4; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
  .terms-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
  .term-item { background: #1e2333; border-radius: 8px; padding: 10px 12px; font-size: 0.75rem; }
  .term-name { font-weight: 700; color: #56b6f7; margin-bottom: 3px; font-size: 0.73rem; }
  .term-desc { color: #b0b8cc; line-height: 1.45; }
</style>
</head>
<body>

<h1>📊 Put Options: Long Put vs Short Put</h1>
<p class="subtitle">A beginner-friendly visual guide to buying & selling put options</p>

<div class="grid">
  <div class="card long">
    <div class="card-title">🟢 Long Put <span class="badge buy">BUY / ASK</span></div>
    <div class="card-role">You are the <strong>Buyer</strong> — you pay the premium</div>
    <div class="analogy-box">🏠 <strong>Think of it like buying home insurance.</strong> You pay a small fee upfront. If something bad happens (price drops), your policy pays out big.</div>
    <div class="stat-row"><span class="stat-label">Action</span><span class="stat-val blue">Buy at the Ask price</span></div>
    <div class="stat-row"><span class="stat-label">Upfront cost</span><span class="stat-val red">Pay premium (e.g. $300)</span></div>
    <div class="stat-row"><span class="stat-label">Max profit</span><span class="stat-val green">Very large (if stock crashes)</span></div>
    <div class="stat-row"><span class="stat-label">Max loss</span><span class="stat-val red">Premium paid only ($300)</span></div>
    <div class="stat-row"><span class="stat-label">You profit when...</span><span class="stat-val green">Stock price falls 📉</span></div>
    <div class="stat-row"><span class="stat-label">Risk level</span><span class="stat-val yellow">⚡ Limited & defined</span></div>
    <div class="chart-wrap">
      <div class="chart-label">P&L Chart — Strike $50, Premium $3</div>
      <svg class="pnl" viewBox="0 0 300 110" xmlns="http://www.w3.org/2000/svg">
        <line x1="20" y1="70" x2="290" y2="70" stroke="#2a2f3e" stroke-width="1.5"/>
        <line x1="20" y1="10" x2="20" y2="105" stroke="#2a2f3e" stroke-width="1.5"/>
        <text x="3" y="73" fill="#9aa0b4" font-size="8">$0</text>
        <polyline points="165,85 290,85" stroke="#e53e3e" stroke-width="2.5" fill="none"/>
        <polyline points="20,10 138,70 165,85" stroke="#1db964" stroke-width="2.5" fill="none"/>
        <polygon points="20,70 20,10 138,70" fill="#1db96420"/>
        <polygon points="165,70 165,85 290,85 290,70" fill="#e53e3e20"/>
        <line x1="138" y1="68" x2="138" y2="72" stroke="#f6c90e" stroke-width="1" stroke-dasharray="3,2"/>
        <text x="25" y="30" fill="#1db964" font-size="8" font-weight="bold">PROFIT ZONE</text>
        <text x="185" y="100" fill="#e53e3e" font-size="8" font-weight="bold">LOSS (capped)</text>
        <text x="100" y="88" fill="#f6c90e" font-size="7">Breakeven $47</text>
        <text x="148" y="84" fill="#9aa0b4" font-size="7">Strike $50</text>
        <text x="12" y="106" fill="#9aa0b4" font-size="7">$30</text>
        <text x="270" y="106" fill="#9aa0b4" font-size="7">$65</text>
      </svg>
    </div>
  </div>

  <div class="card short">
    <div class="card-title">🔴 Short Put <span class="badge sell">SELL / BID</span></div>
    <div class="card-role">You are the <strong>Seller</strong> — you collect the premium</div>
    <div class="analogy-box">🏦 <strong>Think of it like being the insurance company.</strong> You collect fees upfront. Usually it's pure profit — but if disaster hits, you owe big.</div>
    <div class="stat-row"><span class="stat-label">Action</span><span class="stat-val blue">Sell at the Bid price</span></div>
    <div class="stat-row"><span class="stat-label">Upfront income</span><span class="stat-val green">Collect premium (e.g. $300)</span></div>
    <div class="stat-row"><span class="stat-label">Max profit</span><span class="stat-val green">Premium collected only ($300)</span></div>
    <div class="stat-row"><span class="stat-label">Max loss</span><span class="stat-val red">Very large (if stock crashes)</span></div>
    <div class="stat-row"><span class="stat-label">You profit when...</span><span class="stat-val green">Stock stays flat or rises 📈</span></div>
    <div class="stat-row"><span class="stat-label">Risk level</span><span class="stat-val red">🔥 Large & potentially severe</span></div>
    <div class="chart-wrap">
      <div class="chart-label">P&L Chart — Strike $50, Premium $3</div>
      <svg class="pnl" viewBox="0 0 300 110" xmlns="http://www.w3.org/2000/svg">
        <line x1="20" y1="30" x2="290" y2="30" stroke="#2a2f3e" stroke-width="1.5"/>
        <line x1="20" y1="10" x2="20" y2="105" stroke="#2a2f3e" stroke-width="1.5"/>
        <text x="3" y="33" fill="#9aa0b4" font-size="8">$0</text>
        <polyline points="165,18 290,18" stroke="#1db964" stroke-width="2.5" fill="none"/>
        <polyline points="20,100 138,30 165,18" stroke="#e53e3e" stroke-width="2.5" fill="none"/>
        <polygon points="165,30 165,18 290,18 290,30" fill="#1db96420"/>
        <polygon points="20,30 20,100 138,30" fill="#e53e3e20"/>
        <line x1="138" y1="28" x2="138" y2="32" stroke="#f6c90e" stroke-width="1" stroke-dasharray="3,2"/>
        <text x="185" y="14" fill="#1db964" font-size="8" font-weight="bold">PROFIT (capped)</text>
        <text x="22" y="90" fill="#e53e3e" font-size="8" font-weight="bold">LOSS ZONE</text>
        <text x="100" y="45" fill="#f6c90e" font-size="7">Breakeven $47</text>
        <text x="148" y="45" fill="#9aa0b4" font-size="7">Strike $50</text>
        <text x="12" y="106" fill="#9aa0b4" font-size="7">$30</text>
        <text x="270" y="106" fill="#9aa0b4" font-size="7">$65</text>
      </svg>
    </div>
  </div>
</div>

<div class="summary-row">
  <div class="summary-card">
    <h3>🟢 Long Put — Step by Step</h3>
    <div class="step"><div class="step-num g">1</div><div>You think stock XYZ ($52) will fall. You <strong>buy a put</strong> with Strike $50.</div></div>
    <div class="step"><div class="step-num g">2</div><div>You <strong>pay the Ask price</strong> — e.g. $3/share = <strong>$300</strong> per contract (100 shares).</div></div>
    <div class="step"><div class="step-num g">3</div><div>Stock drops to $38. Your put is worth ~$12. Sell for a <strong>$900 profit</strong>! 🎉</div></div>
    <div class="step"><div class="step-num g">4</div><div>If stock rises, option expires worthless. You lose only the $300 premium. That's it.</div></div>
  </div>
  <div class="summary-card">
    <h3>🔴 Short Put — Step by Step</h3>
    <div class="step"><div class="step-num r">1</div><div>You think stock XYZ ($52) will stay flat or rise. You <strong>sell a put</strong> at Strike $50.</div></div>
    <div class="step"><div class="step-num r">2</div><div>You <strong>collect the Bid price</strong> — e.g. $3/share = <strong>$300 income</strong> immediately in your account.</div></div>
    <div class="step"><div class="step-num r">3</div><div>Stock stays above $47 at expiry → you keep the full $300. ✅ Easy win.</div></div>
    <div class="step"><div class="step-num r">4</div><div>But if stock crashes to $30, you must buy at $50. Loss = <strong>$1,700</strong>. ⚠️ Ouch.</div></div>
  </div>
</div>

<div class="terms-section">
  <h3>📖 Key Terms Every Beginner Must Know</h3>
  <div class="terms-grid">
    <div class="term-item"><div class="term-name">🏷️ Bid Price</div><div class="term-desc">The price a buyer offers. When you <em>sell</em> an option, you receive the Bid price.</div></div>
    <div class="term-item"><div class="term-name">🏷️ Ask Price</div><div class="term-desc">The price a seller wants. When you <em>buy</em> an option, you pay the Ask price.</div></div>
    <div class="term-item"><div class="term-name">📄 Put Option</div><div class="term-desc">A contract giving the right to <em>sell</em> 100 shares at the strike price before expiry.</div></div>
    <div class="term-item"><div class="term-name">💰 Premium</div><div class="term-desc">The price of the options contract. Buyer pays it; seller collects it upfront.</div></div>
    <div class="term-item"><div class="term-name">🎯 Strike Price</div><div class="term-desc">The agreed price at which you can buy/sell the stock using the option contract.</div></div>
    <div class="term-item"><div class="term-name">⚖️ Breakeven</div><div class="term-desc">Where you neither profit nor lose. Long Put breakeven = Strike Price − Premium Paid.</div></div>
  </div>
</div>

</body>
</html>