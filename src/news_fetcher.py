import feedparser
import logging
import time
from urllib.parse import quote

logger = logging.getLogger(__name__)


class NewsFetcher:
    def __init__(self):
        # Default English Settings
        self.default_base_url = (
            "https://news.google.com/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en"
        )
        # Yahoo Finance RSS
        self.yahoo_base_url = "https://finance.yahoo.com/rss/headline?s={symbol}"

        # Locale Configurations for Native News
        self.locale_config = {
            "germany": {
                "base_url": "https://news.google.com/rss/search?q={query}&hl=de&gl=DE&ceid=DE:de",
                "native_query": "DAX 40 Wirtschaft",  # 'Economy'
            },
            # We can add Nikkei (JP) or others later if Gemini can parse Japanese
        }

    def fetch_news(
        self, query: str, limit: int = 5, source: str = None, market: str = None
    ) -> str:
        """
        Fetches top news headlines for a specific query from Google and/or Yahoo Finance.
        Supports market-specific locales (e.g., German news for DAX).
        Returns a formatted string suitable for LLM context.
        """
        news_summary = f"--- Top News Headlines for '{query}' ---\n"
        seen_titles = set()
        count = 0

        source = source.lower() if source else None

        # Define 24-hour cutoff
        cutoff_time = time.time() - (24 * 3600)

        # 1. Fetch Google News
        if source is None or source == "google":
            try:
                # Determine URL and Query based on Market Locale
                google_url_template = self.default_base_url
                search_query = query

                if market and market.lower() in self.locale_config:
                    config = self.locale_config[market.lower()]
                    google_url_template = config["base_url"]
                    # Optionally append the native query to the English one, or just use the native one?
                    # Mixing languages in one query usually fails. Let's try fetching the Native one
                    # INSTEAD of the English one if a specific market is requested?
                    # Or maybe we want both?
                    # For now, let's prioritize the Native query if we are in 'native mode'.
                    # But the 'query' arg comes from main.py.

                    # Strategy: If market matches, use the NATIVE query instead of the passed English one
                    # This allows main.py to remain simple.
                    search_query = config["native_query"]
                    logger.info(
                        f"Switched to Native Query for {market}: '{search_query}'"
                    )

                # Enforce strict recency (last 24h)
                full_query = f"{search_query} when:24h"
                formatted_url = google_url_template.format(query=quote(full_query))

                logger.info(f"Fetching Google news for: '{full_query}'")
                feed = feedparser.parse(formatted_url)

                if feed.entries:
                    # Sort entries by published date descending
                    entries = sorted(
                        feed.entries,
                        key=lambda x: x.get("published_parsed") or 0,
                        reverse=True,
                    )

                    news_summary += "Source: Google News\n"
                    for entry in entries:
                        if count >= limit:
                            break

                        # Strict 24h filter
                        pub_struct = entry.get("published_parsed")
                        if pub_struct and time.mktime(pub_struct) < cutoff_time:
                            continue

                        title = entry.title
                        if title not in seen_titles:
                            published = (
                                entry.published
                                if "published" in entry
                                else "Unknown Date"
                            )
                            # Tag foreign headlines so Gemini knows to translate/contextualize
                            prefix = (
                                "[Native] "
                                if market and market.lower() in self.locale_config
                                else ""
                            )
                            news_summary += (
                                f"{count + 1}. {prefix}[{published}] {title}\n"
                            )
                            seen_titles.add(title)
                            count += 1
            except Exception as e:
                logger.error(f"Error fetching Google news: {e}")

        # 2. Fetch Yahoo Finance News (if symbol maps)
        if source is None or source == "yahoo":
            yahoo_symbol = self._get_yahoo_symbol(query)
            if yahoo_symbol:
                try:
                    formatted_url = self.yahoo_base_url.format(symbol=yahoo_symbol)
                    logger.info(f"Fetching Yahoo news for: '{yahoo_symbol}'")
                    feed = feedparser.parse(formatted_url)

                    if feed.entries:
                        # Sort entries by published date descending
                        entries = sorted(
                            feed.entries,
                            key=lambda x: x.get("published_parsed") or 0,
                            reverse=True,
                        )

                        source_header_added = False
                        for entry in entries:
                            if count >= (limit * 2):  # Allow more for combined source
                                break

                            # Strict 24h filter
                            pub_struct = entry.get("published_parsed")
                            if pub_struct and time.mktime(pub_struct) < cutoff_time:
                                continue

                            title = entry.title
                            if title not in seen_titles:
                                if not source_header_added:
                                    news_summary += (
                                        f"\nSource: Yahoo Finance ({yahoo_symbol})\n"
                                    )
                                    source_header_added = True

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
            return "No recent news found (within last 24h)."

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
