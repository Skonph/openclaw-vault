import pytest
from unittest.mock import patch, MagicMock
import io
import sys
from market_data import YahooMarketData, FallbackMarketData, MockMarketData
from positions import Position


def test_yahoo_market_data_underlying_price():
    yahoo = YahooMarketData()
    mock_response = io.BytesIO(
        b'{"quoteResponse": {"result": [{"regularMarketPrice": 530.5}]}}'
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_response
        price = yahoo.underlying_price("SPY")
        assert price == 530.5
        mock_urlopen.assert_called_once()
        # Check User-Agent header is set
        req = mock_urlopen.call_args[0][0]
        assert req.headers.get("User-agent") == "Mozilla/5.0"


def test_yahoo_market_data_underlying_price_failure():
    yahoo = YahooMarketData()

    with patch("urllib.request.urlopen", side_effect=Exception("Timeout")):
        with pytest.raises(RuntimeError) as excinfo:
            yahoo.underlying_price("SPY")
        assert "Yahoo Finance quote request failed" in str(excinfo.value)


def test_yahoo_market_data_implied_vol_and_pnl():
    yahoo = YahooMarketData()
    assert yahoo.implied_vol("SPY") is None
    dummy_pos = Position(
        plan_id="test-1", symbol="SPY", structure="debit_call_spread",
        qty=1, entry_net_price=2.0, max_loss_usd=200, target_profit_usd=300,
        invalidation=None, opened_at="2026-06-06T00:00:00Z"
    )
    assert yahoo.position_pnl(dummy_pos) == 0.0


def test_fallback_market_data_retries_and_succeeds():
    primary = MagicMock()
    primary.underlying_price.side_effect = ConnectionError("Primary Down")
    primary.implied_vol.side_effect = ConnectionError("Primary Down")
    primary.position_pnl.side_effect = ConnectionError("Primary Down")

    secondary = MockMarketData(prices={"SPY": 535.0}, ivs={"SPY": 0.15})
    secondary.position_pnl = MagicMock(return_value=120.0)

    fallback = FallbackMarketData([primary, secondary])

    # 1. underlying_price falls back to secondary
    with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
        price = fallback.underlying_price("SPY")
        assert price == 535.0
        assert "[MagicMock] failed underlying_price for SPY: Primary Down" in mock_stderr.getvalue()

    # 2. implied_vol falls back to secondary
    with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
        iv = fallback.implied_vol("SPY")
        assert iv == 0.15
        assert "[MagicMock] failed implied_vol for SPY: Primary Down" in mock_stderr.getvalue()

    # 3. position_pnl falls back to secondary
    dummy_pos = Position(
        plan_id="test-1", symbol="SPY", structure="debit_call_spread",
        qty=1, entry_net_price=2.0, max_loss_usd=200, target_profit_usd=300,
        invalidation=None, opened_at="2026-06-06T00:00:00Z"
    )
    with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
        pnl = fallback.position_pnl(dummy_pos)
        assert pnl == 120.0
        assert "[MagicMock] failed position_pnl for test-1: Primary Down" in mock_stderr.getvalue()


def test_fallback_market_data_all_fail():
    primary = MagicMock()
    primary.underlying_price.side_effect = ConnectionError("Primary Down")
    primary.position_pnl.side_effect = ConnectionError("Primary Down")

    secondary = MagicMock()
    secondary.underlying_price.side_effect = ConnectionError("Secondary Down")
    secondary.position_pnl.side_effect = ConnectionError("Secondary Down")

    fallback = FallbackMarketData([primary, secondary])

    # 1. underlying_price raises the last exception
    with pytest.raises(ConnectionError) as excinfo:
        fallback.underlying_price("SPY")
    assert "Secondary Down" in str(excinfo.value)

    # 2. position_pnl returns 0.0 to prevent crash loops
    dummy_pos = Position(
        plan_id="test-1", symbol="SPY", structure="debit_call_spread",
        qty=1, entry_net_price=2.0, max_loss_usd=200, target_profit_usd=300,
        invalidation=None, opened_at="2026-06-06T00:00:00Z"
    )
    pnl = fallback.position_pnl(dummy_pos)
    assert pnl == 0.0
