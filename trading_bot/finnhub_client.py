import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

class FinnhubClient:
    """
    Client for interacting with the Finnhub API for Spot Prices and News.
    """
    def __init__(self):
        self.base_url = Config.FINNHUB_API_BASE_URL
        self.api_key = Config.FINNHUB_API_KEY
        self.headers = {
            'X-Finnhub-Token': self.api_key
        }

    def get_quote(self, symbol: str) -> dict:
        """
        Fetch real-time spot price and basic quote for a symbol.
        Returns: { 'c': Current price, 'd': Change, 'dp': Percent change, 
                   'h': High, 'l': Low, 'o': Open, 'pc': Previous close }
        """
        url = f"{self.base_url}/quote?symbol={symbol}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_market_news(self, category: str = "general") -> list:
        """
        Fetch live financial market news.
        Valid categories: general, forex, crypto, merger.
        """
        url = f"{self.base_url}/news?category={category}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()
        
    def get_company_news(self, symbol: str, start_date: str, end_date: str) -> list:
        """
        Fetch company-specific news within a date range (YYYY-MM-DD).
        """
        url = f"{self.base_url}/company-news?symbol={symbol}&from={start_date}&to={end_date}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()
