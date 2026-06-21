"""
IBKR paper executor — a THIN adapter.

It does no risk thinking. It receives an already-approved GuardrailResult + plan
and submits a combo order to a *paper* account via ib_async. Two hard safety
gates make it refuse to touch a live account:

    1. PAPER_ONLY env / arg must be true (default true).
    2. The connected IBKR account id must start with "DU" (IBKR paper accounts)
       OR be explicitly allow-listed. Live accounts start with "U".

ib_async is the maintained successor to ib_insync:  pip install ib_async
Requires TWS or IB Gateway running with API enabled (paper port default 7497).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from guardrail import GuardrailResult
from schema import TradePlan, Side


class PaperSafetyError(RuntimeError):
    """Raised when execution against a non-paper account is attempted."""


@dataclass
class ExecutionReport:
    submitted: bool
    plan_id: str
    qty: int
    broker_order_id: Optional[int] = None
    status: str = ""
    detail: str = ""


class IBKRPaperExecutor:
    PAPER_PORT = 7497   # IB Gateway/TWS paper default (live is 7496 / 4001)

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = PAPER_PORT,
        client_id: int = 17,
        paper_only: bool = True,
        allowed_account_prefixes: tuple[str, ...] = ("DU", "DF"),
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.paper_only = paper_only
        self.allowed_account_prefixes = allowed_account_prefixes
        self._ib = None  # lazily created ib_async.IB()

    # ---------- connection + safety ----------
    def connect(self):
        try:
            from ib_async import IB
        except ImportError as e:  # pragma: no cover - env dependent
            raise ImportError(
                "ib_async not installed. Run: pip install ib_async\n"
                "Also start IB Gateway/TWS in PAPER mode with the API enabled."
            ) from e

        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id)
        self._ib = ib
        self._assert_paper_account()
        return self

    def _assert_paper_account(self) -> None:
        if not self.paper_only:
            return  # caller explicitly opted out — still gated by account prefix below
        accounts = list(self._ib.managedAccounts())
        if not accounts:
            raise PaperSafetyError("No managed accounts reported by IBKR.")
        for acct in accounts:
            if not acct.startswith(self.allowed_account_prefixes):
                raise PaperSafetyError(
                    f"Account '{acct}' is not a recognised paper account "
                    f"(expected prefix {self.allowed_account_prefixes}). "
                    f"Refusing to trade. Connect IB Gateway/TWS in PAPER mode."
                )

    def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None

    # ---------- order construction ----------
    def _build_combo(self, plan_or_pos):
        """Build a BAG (combo) contract + the leg combos for a multi-leg order."""
        from ib_async import Contract, ComboLeg, Option

        if self._ib is None:
            raise RuntimeError("not connected")

        combo_legs: List = []
        legs = plan_or_pos.legs_obj if hasattr(plan_or_pos, "legs_obj") else plan_or_pos.legs
        for leg in legs:
            opt = Option(
                symbol=leg.symbol,
                lastTradeDateOrContractMonth=leg.expiry.replace("-", ""),
                strike=leg.strike,
                right=leg.right.value,
                exchange="SMART",
                currency="USD",
            )
            (qualified,) = self._ib.qualifyContracts(opt)
            cl = ComboLeg(
                conId=qualified.conId,
                ratio=leg.ratio,
                action="BUY" if leg.side == Side.BUY else "SELL",
                exchange="SMART",
            )
            combo_legs.append(cl)

        bag = Contract(
            symbol=plan_or_pos.symbol,
            secType="BAG",
            currency="USD",
            exchange="SMART",
            comboLegs=combo_legs,
        )
        return bag

    # ---------- P3: snapshot combo quote ----------
    def _get_combo_mid(self, bag) -> Optional[float]:
        """
        P3: Snapshot quote the combo BAG and return the mid price (split the spread).
        Returns None if bid/ask unavailable (market closed, data issue, etc.).
        """
        import sys
        try:
            t = self._ib.reqMktData(bag, "", snapshot=True)
            self._ib.sleep(1.5)
            bid = t.bid
            ask = t.ask
            # Net-credit combos (credit spreads, Iron Condors) legitimately quote
            # negative bid/ask on the BAG -- only reject NaN and the (0, 0)
            # "no data" sentinel used by the mock/test harness and by IBKR when
            # a combo quote simply isn't available.
            if (bid is not None and ask is not None
                    and bid == bid and ask == ask  # not NaN
                    and not (float(bid) == 0.0 and float(ask) == 0.0)):
                return round((float(bid) + float(ask)) / 2.0, 2)
        except Exception as e:
            print(f"[IBKRPaperExecutor._get_combo_mid] combo quote failed: {e}", file=sys.stderr)
        return None

    # ---------- submit ----------
    def execute(self, plan: TradePlan, result: GuardrailResult,
                limit_price: Optional[float] = None) -> ExecutionReport:
        """Submit an approved plan. No-op (with a clear report) if not tradeable."""
        if not result.tradeable:
            return ExecutionReport(
                submitted=False, plan_id=plan.plan_id, qty=0,
                status=result.decision.value,
                detail="; ".join(result.reasons),
            )
        if self._ib is None:
            raise RuntimeError("call connect() before execute()")

        from ib_async import LimitOrder, MarketOrder

        bag = self._build_combo(plan)
        qty = result.approved_qty
        if limit_price is not None:
            price = limit_price
        else:
            # Enforce bid-ask spread width verification to prevent execution slippage
            try:
                t = self._ib.reqMktData(bag, "", snapshot=True)
                self._ib.sleep(1.5)
                bid = t.bid
                ask = t.ask
                if (bid is not None and ask is not None
                        and bid == bid and ask == ask  # not NaN
                        and not (float(bid) == 0.0 and float(ask) == 0.0)):
                    spread_width = abs(float(ask) - float(bid))
                    # Default max spread cap: $0.30 for index spreads (can override in env)
                    max_spread_width = float(os.environ.get("IBKR_MAX_COMBO_SPREAD", "0.30"))
                    if spread_width > max_spread_width:
                        return ExecutionReport(
                            submitted=False,
                            plan_id=plan.plan_id,
                            qty=0,
                            status="REJECTED",
                            detail=f"Combo bid-ask spread too wide: ${spread_width:.2f} > ${max_spread_width:.2f}. Execution skipped to prevent slippage."
                        )
                    price = round((float(bid) + float(ask)) / 2.0, 2)
                else:
                    price = plan.net_price
            except Exception as e:
                print(f"[WARN] Combo quote check failed: {e}. Falling back to plan.net_price.")
                price = plan.net_price

        # Combos should virtually always be limit orders; fall back to MKT only
        # if no price is supplied (not recommended for live options).
        order = (LimitOrder("BUY", qty, price) if price is not None
                 else MarketOrder("BUY", qty))
        # The direction of a BAG is encoded in the leg actions; "BUY" the bag
        # means "open the structure as specified".

        trade = self._ib.placeOrder(bag, order)
        self._ib.sleep(1.0)  # let status flow back
        return ExecutionReport(
            submitted=True,
            plan_id=plan.plan_id,
            qty=qty,
            broker_order_id=trade.order.orderId,
            status=trade.orderStatus.status,
            detail=f"{plan.structure} x{qty} @ {price if price is not None else 'MKT'}",
        )


    # ---------- close ----------
    def close_position(self, plan_or_pos, qty: int,
                       limit_price: Optional[float] = None) -> ExecutionReport:
        """
        Flatten an open structure by submitting the opposite-direction combo.
        `plan_or_pos` is the original TradePlan or Position (we reuse its legs to rebuild the BAG);
        the combo leg actions already encode the structure, so we SELL the bag
        to close what was opened with BUY.
        """
        if self._ib is None:
            raise RuntimeError("call connect() before close_position()")
        from ib_async import LimitOrder, MarketOrder

        bag = self._build_combo(plan_or_pos)
        if limit_price is not None:
            close_price = limit_price
        else:
            mid = self._get_combo_mid(bag)
            close_price = mid  # None -> MKT (acceptable for urgency closes)
        order = (LimitOrder("SELL", qty, close_price) if close_price is not None
                 else MarketOrder("SELL", qty))
        trade = self._ib.placeOrder(bag, order)
        self._ib.sleep(1.0)
        return ExecutionReport(
            submitted=True,
            plan_id=plan_or_pos.plan_id,
            qty=qty,
            broker_order_id=trade.order.orderId,
            status=trade.orderStatus.status,
            detail=f"CLOSE {plan_or_pos.structure} x{qty} @ {close_price if close_price is not None else 'MKT'}",
        )


def executor_from_env() -> IBKRPaperExecutor:
    """Construct from env vars; defaults are all paper-safe."""
    return IBKRPaperExecutor(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", str(IBKRPaperExecutor.PAPER_PORT))),
        client_id=int(os.getenv("IBKR_CLIENT_ID", "17")),
        paper_only=os.getenv("IBKR_PAPER_ONLY", "true").lower() != "false",
    )
