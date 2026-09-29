import logging
import os
from openai import OpenAI

logger = logging.getLogger(__name__)

class SentimentEngine:
    """
    NLP Sentiment Engine to parse financial news using Cloud LLMs.
    Defaults to Tokenhub, falls back to OpenRouter.
    """
    def __init__(self):
        # We assume the user adds TOKENHUB_API_KEY to their .env
        self.api_key = os.getenv("TOKENHUB_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        
        if os.getenv("TOKENHUB_API_KEY"):
            # Tokenhub generic proxy endpoint
            self.base_url = "https://api.tokenhub.com/v1" 
            self.model = "gpt-4o-mini"
        elif os.getenv("OPENROUTER_API_KEY"):
            # OpenRouter endpoint
            self.base_url = "https://openrouter.ai/api/v1"
            self.model = "openai/gpt-4o-mini"
        else:
            self.base_url = None
            self.model = None

        if self.api_key and self.base_url:
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key
            )
            logger.info(f"SentimentEngine initialized with {self.base_url}")
        else:
            self.client = None
            logger.warning("No Tokenhub or OpenRouter API key found. SentimentEngine will return 0.0")

    def analyze_news(self, news_articles: list) -> float:
        """
        Takes a list of news dictionaries and returns an aggregated
        sentiment score between -1.0 (very bearish) and 1.0 (very bullish).
        """
        if not news_articles or not self.client:
            return 0.0
            
        # Extract headlines to save token cost, limiting to the 10 most recent to avoid context bloat
        headlines = [article.get('headline', '') for article in news_articles[:10] if isinstance(article, dict)]
        if not headlines:
            return 0.0
            
        logger.info(f"Analyzing {len(headlines)} news articles via {self.model}...")
        
        headlines_text = "\n".join(f"- {h}" for h in headlines if h)
        
        prompt = f"""
        You are a quantitative financial sentiment analyzer. 
        Read the following recent financial headlines for a specific asset.
        
        Headlines:
        {headlines_text}
        
        Output a single float between -1.0 (extremely bearish) and 1.0 (extremely bullish).
        Output ONLY the number, nothing else.
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10
            )
            
            result_text = response.choices[0].message.content.strip()
            score = float(result_text)
            
            # Ensure the score is within bounds
            return max(-1.0, min(1.0, score))
            
        except Exception as e:
            logger.error(f"LLM Sentiment analysis failed: {e}")
            return 0.0
