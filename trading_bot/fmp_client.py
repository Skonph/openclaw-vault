import os
import requests
import logging

logger = logging.getLogger(__name__)

class FMPClient:
    """
    A lightweight, VPS-safe REST client for Financial Modeling Prep (FMP).
    Bypasses Yahoo Finance IP blocks to fetch fundamental data.
    """
    def __init__(self):
        self.api_key = os.getenv("FMP_API_KEY")
        self.base_url = "https://financialmodelingprep.com/stable"
        
        if not self.api_key:
            logger.warning("FMP_API_KEY not found! API calls will fail.")

    def _get(self, endpoint: str, params: dict = None) -> list:
        if params is None:
            params = {}
        params['apikey'] = self.api_key
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"FMP API Error: {e}")
            return []

    def get_financials(self, symbol: str) -> dict:
        """Fetches the most recent Income Statement and Balance Sheet"""
        income = self._get(f"income-statement/{symbol}", {"limit": 1})
        balance = self._get(f"balance-sheet-statement/{symbol}", {"limit": 1})
        return {
            "income_statement": income[0] if isinstance(income, list) and income else {},
            "balance_sheet": balance[0] if isinstance(balance, list) and balance else {}
        }
        
    def get_price_targets(self, symbol: str) -> dict:
        """Fetches Wall Street analyst consensus price targets"""
        targets = self._get(f"price-target-consensus", {"symbol": symbol})
        return targets[0] if isinstance(targets, list) and targets else {}
        
    def get_dividends(self, symbol: str) -> list:
        """Fetches the 5 most recent dividend payouts"""
        dividends = self._get(f"historical-price-full/stock_dividend/{symbol}")
        # FMP sometimes returns a dict with a 'historical' list
        if isinstance(dividends, dict) and "historical" in dividends:
            return dividends["historical"][:5] 
        return []
