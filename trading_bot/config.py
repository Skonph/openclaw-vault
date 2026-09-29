import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
    TRADIER_ACCESS_TOKEN = os.getenv("TRADIER_ACCESS_TOKEN")
    TRADIER_ACCOUNT_ID = os.getenv("TRADIER_ACCOUNT_ID")
    
    # Tradier API URLs 
    # Using production by default for real-time live account data
    TRADIER_API_BASE_URL = "https://api.tradier.com/v1"
    TRADIER_STREAM_URL = "wss://ws.tradier.com/v1/markets/events"
    
    # Finnhub API URLs
    FINNHUB_API_BASE_URL = "https://finnhub.io/api/v1"
    FINNHUB_WEBSOCKET_URL = f"wss://ws.finnhub.io?token={FINNHUB_API_KEY}"
    
    @staticmethod
    def validate():
        missing_keys = []
        if not Config.FINNHUB_API_KEY:
            missing_keys.append("FINNHUB_API_KEY")
        if not Config.TRADIER_ACCESS_TOKEN:
            missing_keys.append("TRADIER_ACCESS_TOKEN")
        if not Config.TRADIER_ACCOUNT_ID:
            missing_keys.append("TRADIER_ACCOUNT_ID")
            
        if missing_keys:
            raise ValueError(f"Missing environment variables: {', '.join(missing_keys)}")
