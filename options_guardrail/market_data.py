"""
Market-data abstraction for the exit monitor.

The monitor only needs three things per tick:
    - underlying price       (for UNDERLYING_ABOVE / UNDERLYING_BELOW)
    - implied vol            (for IV_ABOVE / IV_BELOW)
    - position mark / uPnL    (for profit-target & stop, and realized P&L on close)

Keeping this behind a Protocol means the whole exit engine is testable with
MockMarketData and no IBKR connection. The IBKR-backed provider is a thin
wrapper you swap in for live paper running.
"""

from __future__ import annotations

import sys
from typing import Dict, Optional, Protocol

from positions import Position


class MarketDataProvider(Protocol):
    def underlying_price(self, symbol: str) -> float: ...
    def implied_vol(self, symbol: str) -> Optional[float]: ...
    def position_pnl(self, position: Position) -> float:
        """Current unrealized P&L in USD for the open position (gain +, loss -)."""
        ...


class MockMarketData:
    """Deterministic provider for tests and dry runs. You set the numbers."""

    def __init__(
        self,
        prices: Optional[Dict[str, float]] = None,
        ivs: Optional[Dict[str, float]] = None,
        pnls: Optional[Dict[str, float]] = None,
    ):
        self.prices = dict(prices or {})
        self.ivs = dict(ivs or {})
        self.pnls = dict(pnls or {})  # keyed by plan_id

    def underlying_price(self, symbol: str) -> float:
        if symbol not in self.prices:
            raise KeyError(f"no mock price for {symbol}")
        return self.prices[symbol]

    def implied_vol(self, symbol: str) -> Optional[float]:
        return self.ivs.get(symbol)

    def position_pnl(self, position: Position) -> float:
        return self.pnls.get(position.plan_id, 0.0)

    # convenience for tests
    def set_price(self, symbol: str, px: float) -> None:
        self.prices[symbol] = px

    def set_pnl(self, plan_id: str, pnl: float) -> None:
        self.pnls[plan_id] = pnl


class IBKRMarketData:
    """
    Thin IBKR-backed provider (ib_async). Pass the *connected* IB instance from
    IBKRPaperExecutor so they share one connection.

    Note: getting a precise combo mark requires the leg contracts; here we read
    the BAG's last/mark via a snapshot. For production you may want to value each
    leg and net them. Kept intentionally simple.
    """

    def __init__(self, ib, tradier_client=None):
        self._ib = ib
        self._tradier_client = tradier_client
        self._under_cache: Dict[str, float] = {}

    def underlying_price(self, symbol: str) -> float:  # pragma: no cover - needs IBKR
        from ib_async import Stock
        stk = Stock(symbol, "SMART", "USD")
        (q,) = self._ib.qualifyContracts(stk)
        t = self._ib.reqMktData(q, "", snapshot=True)
        self._ib.sleep(1.0)
        px = t.marketPrice()
        if px and px == px:  # not NaN
            return float(px)
        if t.last and t.last == t.last:
            return float(t.last)
        raise RuntimeError(f"no market price for {symbol}")

    def implied_vol(self, symbol: str) -> Optional[float]:
        # Try Tradier client first (fast, no IBKR round-trip needed)
        if self._tradier_client is not None:
            try:
                iv = self._tradier_client.atm_iv(symbol)
                if iv is not None:
                    return iv
                print(
                    f"[IBKRMarketData.implied_vol] Tradier returned None for {symbol}; "
                    f"falling back to IBKR tick 106",
                    file=sys.stderr,
                )
            except Exception as e:
                print(
                    f"[IBKRMarketData.implied_vol] Tradier atm_iv failed for {symbol}: {e}; "
                    f"falling back to IBKR tick 106",
                    file=sys.stderr,
                )

        # Fall back to IBKR generic tick 106 (option implied volatility)
        try:
            from ib_async import Stock
            stk = Stock(symbol, "SMART", "USD")
            qualified_list = self._ib.qualifyContracts(stk)
            if not qualified_list:
                print(
                    f"[IBKRMarketData.implied_vol] IBKR qualifyContracts returned empty for {symbol}",
                    file=sys.stderr,
                )
                return None
            qualified = qualified_list[0]
            t = self._ib.reqMktData(qualified, "106", snapshot=True)
            self._ib.sleep(2.0)
            iv = t.impliedVolatility
            if iv is not None and iv == iv and iv > 0:  # valid positive, not NaN
                return float(iv)
            print(
                f"[IBKRMarketData.implied_vol] IBKR tick 106 returned invalid IV "
                f"({iv!r}) for {symbol}",
                file=sys.stderr,
            )
            return None
        except Exception as e:
            print(
                f"[IBKRMarketData.implied_vol] IBKR tick 106 failed for {symbol}: {e}",
                file=sys.stderr,
            )
            return None

    def position_pnl(self, position: Position) -> float:
        # Production: value each option leg's mark, net against entry cost * qty.
        if not position.legs:
            # Fallback if no legs persisted (backwards compatibility)
            for item in self._ib.portfolio():
                if item.contract.symbol == position.symbol:
                    return float(item.unrealizedPNL)
            return 0.0

        from ib_async import Option
        from schema import Side

        tickers = []
        for leg in position.legs_obj:
            opt = Option(
                symbol=leg.symbol,
                lastTradeDateOrContractMonth=leg.expiry.replace("-", ""),
                strike=leg.strike,
                right=leg.right.value,
                exchange="SMART",
                currency="USD",
            )
            try:
                qualified_list = self._ib.qualifyContracts(opt)
                if not qualified_list:
                    print(
                        f"[IBKRMarketData.position_pnl] Qualification returned empty list for leg: {leg}",
                        file=sys.stderr,
                    )
                    return 0.0
                qualified = qualified_list[0]
                t = self._ib.reqMktData(qualified, "", snapshot=True)
                tickers.append((leg, t))
            except Exception as e:
                print(
                    f"[IBKRMarketData.position_pnl] Failed to qualify option contract for leg {leg}: {e}",
                    file=sys.stderr,
                )
                return 0.0

        # Dynamic wait: scale with number of tickers, minimum 1.5 s
        self._ib.sleep(max(1.5, len(tickers) * 0.75))

        current_combo_value = 0.0
        for leg, t in tickers:
            bid = t.bid
            ask = t.ask
            if bid and ask and bid == bid and ask == ask and bid > 0 and ask > 0:
                mark = (bid + ask) / 2.0
            elif t.last and t.last == t.last and t.last > 0:
                mark = t.last
            elif t.close and t.close == t.close and t.close > 0:
                mark = t.close
            else:
                px = t.marketPrice()
                mark = float(px) if (px and px == px and px > 0) else 0.0

            leg_val = mark * leg.ratio * (1 if leg.side == Side.BUY else -1)
            current_combo_value += leg_val

        per_leg_pnl = (current_combo_value - position.entry_net_price) * 100.0 * position.qty

        # Cross-check against IBKR portfolio unrealizedPNL (diagnostic only, never raises)
        try:
            for item in self._ib.portfolio():
                if item.contract.symbol == position.symbol:
                    ibkr_upnl = float(item.unrealizedPNL)
                    diff = abs(per_leg_pnl - ibkr_upnl)
                    if diff > 5.0:
                        print(
                            f"[IBKRMarketData.position_pnl] {position.plan_id}: "
                            f"per-leg=${per_leg_pnl:+.2f} vs IBKR portfolio=${ibkr_upnl:+.2f} "
                            f"(diff=${diff:.2f})",
                            file=sys.stderr,
                        )
                    break
        except Exception:
            pass  # cross-check is diagnostic — never propagate

        return per_leg_pnl


class TradierMarketData:
    """
    Tradier-backed market data provider.
    Pulls spot prices, ATM IV, and option leg marks using the TradierClient.
    """

    def __init__(self, client):
        self.client = client

    def underlying_price(self, symbol: str) -> float:
        quotes = self.client.quotes([symbol])
        if not quotes or "last" not in quotes[0]:
            raise RuntimeError(f"no Tradier market price for {symbol}")
        val = quotes[0]["last"]
        if val is None:
            raise RuntimeError(f"Tradier returned None for underlying {symbol} price")
        return float(val)

    def implied_vol(self, symbol: str) -> Optional[float]:
        try:
            return self.client.atm_iv(symbol)
        except Exception:
            return None

    def position_pnl(self, position: Position) -> float:
        if not position.legs:
            return 0.0

        from schema import Side

        # Construct OCC option symbols for each leg
        occ_symbols = []
        for leg in position.legs_obj:
            expiry_parts = leg.expiry.split("-")
            yy = expiry_parts[0][2:]
            mm = expiry_parts[1]
            dd = expiry_parts[2]
            right_char = leg.right.value
            strike_cents = int(round(leg.strike * 1000))
            occ = f"{leg.symbol}{yy}{mm}{dd}{right_char}{strike_cents:08d}"
            occ_symbols.append((leg, occ))

        try:
            # Query quotes from Tradier
            quotes_list = self.client.quotes([occ for _, occ in occ_symbols])
            quotes_by_sym = {q["symbol"]: q for q in quotes_list if "symbol" in q}
        except Exception as e:
            print(f"Tradier quotes fetch failed: {e}")
            return 0.0

        current_combo_value = 0.0
        for leg, occ in occ_symbols:
            q = quotes_by_sym.get(occ)
            if not q:
                mark = 0.0
            else:
                bid = q.get("bid")
                ask = q.get("ask")
                if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
                    mark = (float(bid) + float(ask)) / 2.0
                elif q.get("last") is not None and float(q["last"]) > 0:
                    mark = float(q["last"])
                else:
                    mark = 0.0

            leg_val = mark * leg.ratio * (1 if leg.side == Side.BUY else -1)
            current_combo_value += leg_val

        unrealized_pnl = (current_combo_value - position.entry_net_price) * 100.0 * position.qty
        return unrealized_pnl


class YahooMarketData:
    """Free public Yahoo Finance API fallback for spot prices."""

    def underlying_price(self, symbol: str) -> float:
        import urllib.request
        import json
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                result = data.get('quoteResponse', {}).get('result', [])
                if not result:
                    raise ValueError(f"No results for {symbol} on Yahoo Finance")
                val = result[0].get('regularMarketPrice')
                if val is None:
                    raise ValueError(f"Yahoo Finance returned null regularMarketPrice for {symbol}")
                return float(val)
        except Exception as e:
            raise RuntimeError(f"Yahoo Finance quote request failed for {symbol}: {e}") from e

    def implied_vol(self, symbol: str) -> Optional[float]:
        return None

    def position_pnl(self, position: Position) -> float:
        return 0.0


class FallbackMarketData:
    """
    Chainable MarketDataProvider wrapper.
    Queries providers in sequence; falls back to the next if one throws an exception.
    """

    def __init__(self, providers: list[MarketDataProvider]):
        self.providers = [p for p in providers if p is not None]

    def underlying_price(self, symbol: str) -> float:
        last_err = None
        for p in self.providers:
            try:
                return p.underlying_price(symbol)
            except Exception as e:
                import sys
                print(f"[{p.__class__.__name__}] failed underlying_price for {symbol}: {e}", file=sys.stderr)
                last_err = e
        if last_err:
            raise last_err
        raise RuntimeError(f"No market data providers available to query underlying_price for {symbol}")

    def implied_vol(self, symbol: str) -> Optional[float]:
        for p in self.providers:
            try:
                val = p.implied_vol(symbol)
                if val is not None:
                    return val
            except Exception as e:
                import sys
                print(f"[{p.__class__.__name__}] failed implied_vol for {symbol}: {e}", file=sys.stderr)
        return None

    def position_pnl(self, position: Position) -> float:
        for p in self.providers:
            try:
                return p.position_pnl(position)
            except Exception as e:
                import sys
                print(f"[{p.__class__.__name__}] failed position_pnl for {position.plan_id}: {e}", file=sys.stderr)
        return 0.0
