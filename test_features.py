"""Validate openclaw_features.py against the REAL Ford data pulled from IBKR live."""
import datetime as dt
from openclaw_features import FeatureExtractor, run_batch

TODAY = dt.date(2026, 6, 25)

# --- stubs seeded with the actual live IBKR responses captured this session ---

def search_contracts(query, security_type="STK"):
    return {"results": [{
        "underlying_contract_id": 9599491, "symbol": "F", "country_code": "US",
        "sections": [{"security_type": "STK"}, {"security_type": "OPT"}],
    }]}

def get_price_snapshot(contract_id, market_data_names):
    # underlying-level snapshot (real captured values)
    if contract_id == 9599491:
        return {
            "last": {"price": 14.17},
            "historical-vol": {"annual_pct": 0.5145150242606332},
            "implied-vol-underlying": {"annual_iv": 0.381203247188816, "is_valid": True},
            "implied-volatility-percentile": {"high_13w": 0.3968254, "high_52w": 0.78486055},
            "underlying-today-option-volume": {"callVolume": 13569, "putVolume": 6270},
            "underlying-avg-option-volume": {"avgCallVolume": 105830, "avgPutVolume": 44448},
        }
    # per-contract option snapshots (synthetic but realistic for F)
    # contract ids encode: 1xx=front ATM, 2xx=back ATM, 3xx=OTM put, 4xx=OTM call
    table = {
        101: 0.355, 102: 0.360,        # front ATM call/put ~22 DTE
        201: 0.372, 202: 0.378,        # back ATM call/put ~57 DTE (contango)
        301: 0.430,                    # OTM put (higher iv = put skew)
        401: 0.345,                    # OTM call
    }
    iv = table.get(contract_id, 0.36)
    oi = {"call": 0, "put": 0}
    if contract_id == 301: oi = {"call": 0, "put": 8200}
    if contract_id == 401: oi = {"call": 5100, "put": 0}
    return {
        "option-midpoint-iv": {"isValid": True, "value": iv},
        "implied-vol": {"value": iv},
        "option-open-interest": oi,
    }

def get_option_parameters(underlying_contract_id):
    return {"current_exchange": "SMART", "expirations": [
        {"id": "exp_0626", "date": "20260626", "regular": False, "trading_class": "F"},
        {"id": "exp_0717", "date": "20260717", "regular": True,  "trading_class": "F"},
        {"id": "exp_0821", "date": "20260821", "regular": True,  "trading_class": "F"},
        {"id": "exp_0918", "date": "20260918", "regular": True,  "trading_class": "F"},
    ]}

def get_option_data(expiration_id, min_strike=None, max_strike=None):
    # front (0717, ~22 DTE) and back (0821, ~57 DTE) ATM chains around 14.17
    if expiration_id == "exp_0717":
        return {"rows": [
            {"strike": "12.0", "call_contract_id": 401, "put_contract_id": 301},
            {"strike": "14.0", "call_contract_id": 101, "put_contract_id": 102},
            {"strike": "16.5", "call_contract_id": 401, "put_contract_id": 301},
        ]}
    if expiration_id == "exp_0821":
        return {"rows": [
            {"strike": "14.0", "call_contract_id": 201, "put_contract_id": 202},
        ]}
    return {"rows": []}

ex = FeatureExtractor(search_contracts, get_price_snapshot,
                      get_option_parameters, get_option_data, today=TODAY)

import json
print(json.dumps(run_batch(["F"], ex), indent=2))
