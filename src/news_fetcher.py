import feedparser
import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)


class NewsFetcher:
    def __init__(self):
        # Google News RSS
        self.google_base_url = (
            "https://news.google.com/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en"
        )
        # Yahoo Finance RSS
        self.yahoo_base_url = "https://finance.yahoo.com/rss/headline?s={symbol}"

    def fetch_news(self, query: str, limit: int = 5) -> str:
        """
        Fetches top news headlines for a specific query from Google and Yahoo Finance.
        Returns a formatted string suitable for LLM context.
        """
        news_summary = f"--- Top News Headlines for '{query}' ---\n"
        seen_titles = set()
        count = 0

        # 1. Fetch Google News
        try:
            # Enforce recency (last 48h) to avoid stale "relevance" matches
            # 'scoring=n' (Newest) can sometimes be too noisy, so we use 'when:2d' with default sort.
            google_query = f"{query} when:2d"
            formatted_url = self.google_base_url.format(query=quote(google_query))
            logger.info(f"Fetching Google news for: '{google_query}'")
            feed = feedparser.parse(formatted_url)

            if feed.entries:
                news_summary += "Source: Google News\n"
                for entry in feed.entries[:limit]:
                    title = entry.title
                    if title not in seen_titles:
                        published = (
                            entry.published if "published" in entry else "Unknown Date"
                        )
                        news_summary += f"{count + 1}. [{published}] {title}\n"
                        seen_titles.add(title)
                        count += 1
        except Exception as e:
            logger.error(f"Error fetching Google news: {e}")

        # 2. Fetch Yahoo Finance News (if symbol maps)
        yahoo_symbol = self._get_yahoo_symbol(query)
        if yahoo_symbol:
            try:
                formatted_url = self.yahoo_base_url.format(symbol=yahoo_symbol)
                logger.info(f"Fetching Yahoo news for: '{yahoo_symbol}'")
                feed = feedparser.parse(formatted_url)

                if feed.entries:
                    news_summary += f"\nSource: Yahoo Finance ({yahoo_symbol})\n"
                    for entry in feed.entries[:limit]:
                        title = entry.title
                        if title not in seen_titles:
                            published = (
                                entry.published
                                if "published" in entry
                                else "Unknown Date"
                            )
                            news_summary += f"{count + 1}. [{published}] {title}\n"
                            seen_titles.add(title)
                            count += 1
            except Exception as e:
                logger.error(f"Error fetching Yahoo news: {e}")

        if count == 0:
            return "No recent news found."

        return news_summary

    def _get_yahoo_symbol(self, query: str) -> str:
        """
        Maps a search query to a Yahoo Finance ticker symbol.
        """
        q = query.lower()
        if "ftse" in q:
            return "^FTSE"
        elif "s&p" in q or "spx" in q or "500" in q:
            return "^GSPC"
        elif "nikkei" in q or "japan" in q:
            return "^N225"
        elif "gbp" in q:
            return "GBPUSD=X"
        elif "eur" in q:
            return "EURUSD=X"
        elif "dax" in q:
            return "^GDAXI"
        elif "nasdaq" in q or "tech" in q:
            return "^NDX"
        elif "asx" in q or "australia" in q:
            return "^AXJO"
        return None


if __name__ == "__main__":
    # Manual Test
    logging.basicConfig(level=logging.INFO)
    fetcher = NewsFetcher()
    print(fetcher.fetch_news("FTSE 100"))
