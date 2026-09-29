import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

class TradierClient:
    """
    Client for interacting with the Tradier API for Positions, Options Chains, and Greeks.
    """
    def __init__(self):
        self.base_url = Config.TRADIER_API_BASE_URL
        self.access_token = Config.TRADIER_ACCESS_TOKEN
        self.account_id = Config.TRADIER_ACCOUNT_ID
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }

    def get_positions(self) -> list:
        """
        Fetch current account positions and P&L data.
        """
        url = f"{self.base_url}/accounts/{self.account_id}/positions"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        data = response.json()
        if data['positions'] == 'null' or not data['positions']:
            return []
        
        # Depending on if there's one or multiple positions, Tradier returns a dict or list
        positions = data['positions']['position']
        if isinstance(positions, dict):
            return [positions]
        return positions

    def get_options_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list:
        """
        Fetch the options chain for a specific symbol and expiration date.
        If greeks=True, the response will include delta, theta, gamma, vega, rho, and IV.
        """
        url = f"{self.base_url}/markets/options/chains"
        params = {
            'symbol': symbol,
            'expiration': expiration,
            'greeks': 'true' if greeks else 'false'
        }
        
        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        if data['options'] == 'null' or not data['options']:
            return []
            
        options = data['options']['option']
        if isinstance(options, dict):
            return [options]
        return options
        
    def get_options_expirations(self, symbol: str) -> list:
        """
        Fetch available expiration dates for a symbol.
        """
        url = f"{self.base_url}/markets/options/expirations?symbol={symbol}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        data = response.json()
        if data['expirations'] == 'null' or not data['expirations']:
            return []
            
        dates = data['expirations']['date']
        if isinstance(dates, str):
            return [dates]
        return dates
