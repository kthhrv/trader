import logging
from datetime import datetime, timezone
import pandas as pd
import pandas_ta as ta
from typing import Dict, Any

from src.ig_client import IGClient
from src.news_fetcher import NewsFetcher

logger = logging.getLogger(__name__)


class MarketDataError(Exception):
    """Raised when critical market data cannot be fetched."""

    pass


class MarketDataProvider:
    def __init__(
        self,
        ig_client: IGClient,
        news_fetcher: NewsFetcher,
        vix_epic: str = "CC.D.VIX.USS.IP",
    ):
        self.client = ig_client
        self.news_fetcher = news_fetcher
        self.vix_epic = vix_epic

    def get_market_context(
        self, epic: str, news_query: str = None, strategy_name: str = "Market Open"
    ) -> str:
        """
        Orchestrates fetching of all required data (Daily, 15m, 5m, 1m, News, Sentiment)
        and formats it into the prompt string for the Analyst.
        """
        logger.info(f"Fetching market context for {epic}...")

        # 1. Fetch Price Data
        df_daily = self._fetch_daily_data(epic)
        df_15m = self._fetch_15m_data(epic)
        df_5m = self._fetch_granular_data(epic)
        df_1m = self._fetch_timing_data(epic)

        # 2. Calculate Indicators on 15m (Primary Trend)
        df_15m, indicators = self._calculate_indicators(df_15m)

        # 3. Fetch Auxiliary Data (VIX, Sentiment)
        vix_context = self._fetch_vix_context()
        sentiment_context = self._fetch_sentiment_context(epic)

        # 4. Fetch News
        news_context = self._fetch_news(epic, news_query)

        # 5. Build Context String
        return self._format_context_string(
            epic,
            df_daily,
            df_15m,
            df_5m,
            df_1m,
            indicators,
            vix_context,
            sentiment_context,
            news_context,
        )

    def _fetch_daily_data(self, epic: str) -> pd.DataFrame:
        try:
            df = self.client.fetch_historical_data(epic, "D", 10)
            if df.empty:
                raise MarketDataError(f"No daily data received for {epic}")
            return df
        except Exception as e:
            logger.error(f"Error fetching daily data: {e}")
            raise MarketDataError(
                f"Critical: Failed to fetch daily data for {epic}"
            ) from e

    def _fetch_15m_data(self, epic: str) -> pd.DataFrame:
        try:
            # Fetch 50 points to allow for indicator calculation (RSI/ATR)
            df = self.client.fetch_historical_data(epic, "15Min", 50)
            if df.empty:
                raise MarketDataError(f"No 15m data received for {epic}")
            return df
        except Exception as e:
            logger.error(f"Error fetching 15m data: {e}")
            raise MarketDataError(
                f"Critical: Failed to fetch 15m data for {epic}"
            ) from e

    def _fetch_granular_data(self, epic: str) -> pd.DataFrame:
        try:
            # 24 points = 2 hours of 5m data
            df = self.client.fetch_historical_data(epic, "5Min", 24)
            if df.empty:
                raise MarketDataError(f"No 5m data received for {epic}")
            return df
        except Exception as e:
            logger.error(f"Error fetching 5m data: {e}")
            raise MarketDataError(
                f"Critical: Failed to fetch 5m data for {epic}"
            ) from e

    def _fetch_timing_data(self, epic: str) -> pd.DataFrame:
        try:
            # 15 points = 15 mins of 1m data
            df = self.client.fetch_historical_data(epic, "1Min", 15)
            if df.empty:
                raise MarketDataError(f"No 1m data received for {epic}")
            return df
        except Exception as e:
            logger.error(f"Error fetching 1m data: {e}")
            raise MarketDataError(
                f"Critical: Failed to fetch 1m data for {epic}"
            ) from e

    def _calculate_indicators(self, df: pd.DataFrame) -> tuple[pd.DataFrame, Dict]:
        """
        Calculates ATR, RSI, EMA on the provided DataFrame (usually 15m).
        Returns the modified DataFrame and a dictionary of latest values.
        """
        if df.empty:
            return df, {}

        # Ensure numeric columns
        cols = ["open", "high", "low", "close", "volume"]
        existing_cols = [c for c in cols if c in df.columns]
        df[existing_cols] = df[existing_cols].apply(pd.to_numeric, errors="coerce")

        try:
            df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
            df["RSI"] = ta.rsi(df["close"], length=14)
            df["EMA_20"] = ta.ema(df["close"], length=20)
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return df, {}

        # Extract latest values for context summary
        latest = df.iloc[-1]
        prev_close = (
            df.iloc[-2]["close"] if len(df) >= 2 else latest["close"]
        )  # Fallback

        avg_atr = df["ATR"].mean() if "ATR" in df.columns else 0
        current_atr = latest.get("ATR", 0)
        vol_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0

        vol_state = "MEDIUM"
        if vol_ratio < 0.8:
            vol_state = "LOW (Caution: Market may be choppy/ranging)"
        elif vol_ratio > 1.2:
            vol_state = "HIGH (Caution: Expect wider swings)"

        indicators = {
            "atr": current_atr,
            "avg_atr": avg_atr,
            "vol_state": vol_state,
            "vol_ratio": vol_ratio,
            "rsi": latest.get("RSI", 0),
            "ema_20": latest.get("EMA_20", 0),
            "close": latest["close"],
            "prev_close": prev_close,
        }
        return df, indicators

    def _fetch_vix_context(self) -> str:
        try:
            vix_data = self.client.service.fetch_market_by_epic(self.vix_epic)
            if vix_data and "snapshot" in vix_data:
                vix_bid = vix_data["snapshot"].get("bid")
                if vix_bid:
                    return f"VIX Level: {vix_bid} (Market Fear Index)\n"
        except Exception as e:
            logger.warning(f"Failed to fetch VIX data: {e}")
        return ""

    def _fetch_sentiment_context(self, epic: str) -> str:
        try:
            market_details = self.client.data_service.fetch_market_by_epic(epic)
            if market_details and "instrument" in market_details:
                market_id = market_details["instrument"]["marketId"]
                sentiment = (
                    self.client.data_service.fetch_client_sentiment_by_instrument(
                        market_id
                    )
                )

                if sentiment:
                    long_pct = float(sentiment.get("longPositionPercentage", 0))
                    short_pct = float(sentiment.get("shortPositionPercentage", 0))

                    signal_hint = "NEUTRAL"
                    if long_pct > 70:
                        signal_hint = "BEARISH CONTRA (Retail is Crowded Long)"
                    elif short_pct > 70:
                        signal_hint = "BULLISH CONTRA (Retail is Crowded Short)"

                    return (
                        f"\n--- Client Sentiment (IG Markets - % of Accounts) ---\n"
                        f"Long: {long_pct}%\n"
                        f"Short: {short_pct}%\n"
                        f"Signal Implication: {signal_hint}\n"
                    )
        except Exception as e:
            logger.warning(f"Failed to fetch Client Sentiment: {e}")
        return ""

    def _fetch_news(self, epic: str, query: str = None) -> str:
        q = query if query else self._get_default_news_query(epic)
        news_result = self.news_fetcher.fetch_news(q)
        if not news_result or "Error fetching news" in news_result:
            raise MarketDataError(f"Critical: Failed to fetch news for {q}")
        return news_result

    def _get_default_news_query(self, epic: str) -> str:
        # Same logic as StrategyEngine._get_news_query
        if "FTSE" in epic:
            return "FTSE 100 UK Economy"
        elif "SPX" in epic or "US500" in epic:
            return "S&P 500 US Economy"
        elif "GBP" in epic:
            return "GBP USD Forex"
        elif "EUR" in epic:
            return "EUR USD Forex"
        elif "DAX" in epic or "DE30" in epic:
            return "DAX 40 Germany Economy"
        else:
            parts = epic.split(".")
            if len(parts) > 2:
                return f"{parts[2]} Market News"
            return "Global Financial Markets"

    def _format_context_string(
        self,
        epic: str,
        df_daily: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame,
        df_1m: pd.DataFrame,
        indicators: Dict[str, Any],
        vix_context: str,
        sentiment_context: str,
        news_context: str,
    ) -> str:
        # Calculate session stats if possible (Session High/Low)
        # We need "Today's" data.
        today_str = datetime.now().date().isoformat()
        session_high = None
        session_low = None

        # Try to filter 15m data for today's session
        if not df_15m.empty:
            try:
                # Ensure index is DatetimeIndex before filtering
                if isinstance(df_15m.index, pd.DatetimeIndex):
                    df_today = df_15m[df_15m.index.strftime("%Y-%m-%d") == today_str]
                    if not df_today.empty:
                        session_high = df_today["high"].max()
                        session_low = df_today["low"].min()
            except Exception:
                pass

        latest_close = indicators.get("close", 0)
        gap_percent = 0.0
        yesterday_close = indicators.get("prev_close", 0)

        # Try to get yesterday close from Daily data for better gap calculation
        if not df_daily.empty and len(df_daily) >= 2:
            yesterday_close = df_daily.iloc[-2]["close"]

        if yesterday_close > 0:
            gap_percent = ((latest_close - yesterday_close) / yesterday_close) * 100

        gap_str = f"{gap_percent:+.2f}%"

        # Build String
        context = f"Current Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
        context += f"Instrument: {epic}\n"

        if not df_daily.empty:
            context += "\n--- Daily OHLC Data (Last 10 Days) ---\n"
            context += df_daily.to_string()
            context += "\n"

        context += "\n--- Recent OHLC Data (Last 12 Hours, 15m intervals) ---\n"
        context += df_15m.to_string()
        context += "\n"

        if not df_5m.empty:
            context += "\n--- Granular OHLC Data (Last 2 Hours, 5m intervals) ---\n"
            context += df_5m.to_string()
            context += "\n"

        if not df_1m.empty:
            context += "\n--- Timing OHLC Data (Last 15 Minutes, 1m intervals) ---\n"
            context += df_1m.to_string()
            context += "\n"

        context += "\n\n--- Session Context (Today so far) ---\n"
        if session_high is not None and session_low is not None:
            context += f"Today's High: {session_high}\n"
            context += f"Today's Low:  {session_low}\n"
            position_in_range = 0
            if session_high != session_low:
                position_in_range = int(
                    ((latest_close - session_low) / (session_high - session_low)) * 100
                )
            context += (
                f"Current Position in Range: {position_in_range}% (0%=Low, 100%=High)\n"
            )
        else:
            context += "Today's intraday high/low data not yet established.\n"

        context += "\n--- Technical Indicators (Latest Candle) ---\n"
        context += f"RSI (14): {indicators.get('rsi', 0):.2f}\n"
        context += f"ATR (14): {indicators.get('atr', 0):.2f}\n"
        context += f"Avg ATR (Last 50): {indicators.get('avg_atr', 0):.2f}\n"
        context += f"Volatility Regime: {indicators.get('vol_state', 'N/A')} (Current/Avg Ratio: {indicators.get('vol_ratio', 0):.2f})\n"
        context += f"EMA (20): {indicators.get('ema_20', 0):.2f}\n"
        context += f"Current Close: {latest_close}\n"
        context += f"Gap (Open vs Prev Close): {gap_str}\n"

        ema_val = indicators.get("ema_20", 0)
        trend_context = "Unknown"
        if ema_val > 0:
            trend_context = (
                "Price > EMA20 (Bullish)"
                if latest_close > ema_val
                else "Price < EMA20 (Bearish)"
            )
        context += f"Trend Context: {trend_context}\n"

        if vix_context:
            context += vix_context

        if sentiment_context:
            context += sentiment_context

        context += f"\n\n{news_context}"

        return context
