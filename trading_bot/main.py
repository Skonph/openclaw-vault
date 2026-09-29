import logging
import time
from config import Config
from finnhub_client import FinnhubClient
from tradier_client import TradierClient
from scanner import MultiDimensionScanner
from sentiment import SentimentEngine
from verdict import VerdictEngine

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    logger.info("Initializing Bot Architecture (Option 2: Tradier & Finnhub)...")
    
    # 1. Validate Config (Will throw ValueError if keys are missing)
    try:
        Config.validate()
    except ValueError as e:
        logger.warning(f"Configuration warning: {e}")
        logger.warning("Proceeding in stub mode (API calls will fail).")
    
    # 2. Initialize Clients
    finnhub = FinnhubClient()
    tradier = TradierClient()
    
    # 3. Initialize Engines
    scanner = MultiDimensionScanner(tradier, finnhub)
    sentiment_engine = SentimentEngine()
    verdict_engine = VerdictEngine()
    
    # Example Watchlist / Universe
    universe = ["SPY", "QQQ", "IWM", "VIX", "NVDA"]
    
    logger.info("--- Starting Core Loop ---")
    
    # We will only run once for demonstration purposes to avoid infinite loops
    try:
        # Step 1: Scan Market
        logger.info("Scanning market universe...")
        watchlist = scanner.generate_watchlist(universe)
        logger.info(f"Scanner produced watchlist: {watchlist}")
        
        # Step 2: Evaluate Watchlist
        for symbol in watchlist:
            # Fetch recent news for the symbol (stubbed in sentiment engine if API fails)
            news = [] 
            sentiment_score = sentiment_engine.analyze_news(news)
            
            # Assume scanner returns metrics in a real app, passing dummy dict here
            metrics = {"iv_rank": 65} 
            
            verdict = verdict_engine.calculate_verdict(symbol, metrics, sentiment_score)
            logger.info(f"Final Verdict for {symbol}: {verdict}")
            
        logger.info("Core loop iteration complete. Exiting demonstration.")
        
    except Exception as e:
        logger.error(f"Error in core loop: {e}")

if __name__ == "__main__":
    main()
