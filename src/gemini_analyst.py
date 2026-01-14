import json
import logging
import pandas as pd
import typing_extensions as typing
from enum import Enum
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    ERROR = "ERROR"


class EntryType(str, Enum):
    INSTANT = "INSTANT"


class NewsQuality(BaseModel):
    score: int = Field(
        description="Quality score from 0 (useless) to 10 (highly actionable)."
    )
    relevance: str = Field(
        description="Assessment of how relevant the news is to the specific market."
    )
    sentiment_clarity: str = Field(
        description="How clear the sentiment is (High/Medium/Low)."
    )
    reasoning: str = Field(description="Brief explanation of the score.")


class TradingSignal(BaseModel):
    ticker: str = Field(description="The ticker symbol of the asset analyzed.")

    action: Action = Field(description="The trading action recommendation.")

    entry: float = Field(description="The suggested entry price level.")

    entry_type: EntryType = Field(
        description="The type of entry: 'INSTANT' (execute immediately when price touches level). All trades now use INSTANT entry for maximum wave capture."
    )

    stop_loss: float = Field(description="The stop loss price level.")

    take_profit: typing.Optional[float] = Field(
        description="The take profit price level. Can be null if using a trailing stop for uncapped wins."
    )

    size: float = Field(description="The suggested trade size per point.")

    atr: float = Field(
        description="The Average True Range (ATR) at the time of analysis."
    )

    use_trailing_stop: bool = Field(
        description="Whether to use a dynamic trailing stop (True) or a fixed take profit (False). Set to True for breakout/trend strategies to maximize runs. Set to False for range/mean-reversion strategies where price is expected to reverse at target."
    )

    confidence: str = Field(
        description="Confidence level of the analysis (e.g., 'high', 'medium', 'low')."
    )

    reasoning: str = Field(
        description="Brief explanation of the trade rationale based on technicals."
    )


class GeminiAnalyst:
    def __init__(self, model_name: str = "gemini-3-flash-preview"):
        """
        Initializes the Gemini Analyst with a Vertex AI model, using the google-genai SDK.
        """

        self.model_name = model_name

        # Initialize the client directly
        self.client = genai.Client(api_key=GEMINI_API_KEY)

        self.system_instruction = """
            You are a Senior Momentum Trader specializing in "Open Drive" breakout strategies for global indices.
            Your objective is to identify high-probability breakout setups during the market open (first 90 mins).

            ### 1. Market Analysis Protocol
            Analyze the provided Market Context (OHLC, Indicators, Session Data) and News to determine the Market Regime:
            - **High Volatility (ATR > Avg):** Favor **BREAKOUTS** (Trend Following). Look for strong momentum pushing through Key Levels.
            - **Low Volatility (ATR < Avg):** Favor **MEAN REVERSION** (Fade Extremes) or **WAIT**. Breakouts often fail here ("Fake-outs").
            - **Coiling:** If price is consolidating (narrowing range), anticipate an imminent volatility expansion (Breakout).
            - **Granular Structure (5m Data):** Use the provided 5-minute candles to identify micro-structure, specifically checking for "Wick Rejections" or "V-Shape Reversals" that the 15-minute chart might hide. Ensure your entry isn't into a recent micro-rejection.
            - **Precision Timing (1m Data):** Use the 1-minute candles for ultimate entry pinpointing. Identify if the price is currently stalling, rejecting, or accelerating at your proposed entry level. 1-minute wicks are the most reliable indicators of immediate liquidity sweeps.

            ### 2. Trading Rules (Strict)
            - **Direction:** Trade WITH the momentum (Open > EMA20 = Bullish bias, unless overextended).
            - **Extension Rule (No Chasing):** Do NOT recommend a trade if the entry price is more than **1.5x ATR** away from the 20-period EMA. Wait for a pullback or return 'WAIT'.
            - **Entry:** MUST be a specific price level where the "Wave" begins (e.g., break of Pre-Market High/Low).
            - **Stop Loss (Risk):**
                - **HARD RULE:** The Stop Loss MUST be at least **1.5x ATR** away from the entry price, regardless of nearby technical levels.
                - **Structural Placement:** Place beyond Swing High/Low or Key Moving Averages, BUT ensure the distance meets the 1.5x ATR minimum. If the structural level is too close (e.g., 10 points away when ATR is 15), you MUST add padding to reach >1.5x ATR.
                - **High Volatility Regime:** When ATR > Average, increase minimum distance to **2.0x ATR** to survive "stop runs".
                - **Pre-Open/Opening Flush:** Do NOT place stops exactly at the High/Low of the pre-market session. Add a buffer (0.5x ATR) *beyond* the Wick to avoid liquidity sweeps.
                - **MAXIMUM DISTANCE:** 5.0x ATR (If structural stop requires >5x ATR, return 'WAIT').
            - **Take Profit / Management:**
                - **Trend Days:** Use `use_trailing_stop=True` for uncapped upside.
                - **Range Days:** Use `use_trailing_stop=False` and target a fixed Resistance/Support level (R:R > 1.5).

            ### 3. Contrarian Checks
            - **Retail Sentiment:** If >70% Long, be cautious of Longs (Crowded Trade). If >70% Short, look for Short Squeezes.
            - **News:** High-Impact Negative News overrides Bullish Technicals (and vice versa).

            ### 4. Output Format
            - Think deeply about the setup using your internal monologue.
            - Output the final decision ONLY as a structured JSON object matching the requested schema.
            - If the setup is unclear, weak, or violates rules, return `action: "WAIT"`.
            """

    @retry(
        stop=stop_after_attempt(2),  # Try once, then retry once = 2 attempts total
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((errors.ServerError, errors.APIError)),
        reraise=True,  # Let the final exception bubble up to be caught by the try/except block inside
    )
    def analyze_market(
        self,
        market_data_context: str,
        strategy_name: str = "Market Open",
    ) -> typing.Optional[TradingSignal]:
        """
        Sends market data to Gemini and returns a structured TradingSignal.
        """
        try:
            prompt = f"Analyze the following {strategy_name} market data and generate a trading signal:\n\n{market_data_context}"

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    response_mime_type="application/json",
                    response_schema=TradingSignal.model_json_schema(),
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=True,
                        thinking_level="HIGH",
                    ),
                ),
            )

            # Log thoughts if available for transparency
            for part in response.candidates[0].content.parts:
                if part.thought:
                    logger.info(
                        f"--- Gemini Analysis Thoughts ---\n{part.text}\n-------------------------------"
                    )

            # The SDK with response_schema automatically handles the schema enforcement
            if not response.text:
                logger.error("Gemini returned empty response text.")
                return None

            signal_data = json.loads(response.text)
            # Handle potential missing entry_type from older models or if omitted (default fallback)
            if "entry_type" not in signal_data:
                signal_data["entry_type"] = EntryType.INSTANT
            if "use_trailing_stop" not in signal_data:
                signal_data["use_trailing_stop"] = (
                    True  # Default to True (original behavior)
                )
            return TradingSignal(**signal_data)

        except (errors.ClientError, json.JSONDecodeError) as e:
            logger.error(f"Non-retriable error during Gemini analysis: {e}")
            return None
        except Exception as e:
            # If the error is a retriable one, reraise it to trigger @retry
            if isinstance(e, (errors.ServerError, errors.APIError)):
                raise e
            logger.error(f"Unexpected error during Gemini analysis: {e}")
            return None

    def assess_news_quality(
        self,
        news_text: str,
        market_name: str,
    ) -> typing.Optional[NewsQuality]:
        """
        Asks Gemini to rate the quality and relevance of the fetched news for a specific market.
        """
        try:
            prompt = f"""
            Analyze the quality of the following news headlines for trading the '{market_name}' market.
            
            Criteria for High Score (8-10):
            - Recent (within last 24 hours).
            - Highly relevant to the specific asset/index (not just generic global news).
            - Contains substantive economic data or strong sentiment drivers.
            
            Criteria for Low Score (0-4):
            - Old/Stale news.
            - Irrelevant or tangentially related.
            - "Fluff" or clickbait with no market substance.
            
            News Content:
            {news_text}
            """

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=NewsQuality.model_json_schema(),
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=True,
                        thinking_level="HIGH",
                    ),
                ),
            )

            # Log thoughts if available
            for part in response.candidates[0].content.parts:
                if part.thought:
                    logger.info(
                        f"--- Gemini News Assessment Thoughts ---\n{part.text}\n-------------------------------"
                    )

            if not response.text:
                logger.error("Gemini returned empty response for news assessment.")
                return None

            return NewsQuality(**json.loads(response.text))

        except Exception as e:
            logger.error(f"Error during news assessment: {e}")
            return None

    def generate_post_mortem(
        self,
        trade_data: dict,
        price_history_df: pd.DataFrame = None,
    ) -> str:
        """
        Generates a post-mortem analysis for a completed trade.
        """
        log = trade_data.get("log", {})
        monitor = trade_data.get("monitor", [])

        # Summarize monitoring data with fallbacks from log
        if monitor:
            start_price = monitor[0]["bid"]
            end_price = monitor[-1]["bid"]
            min_pnl = min((row["pnl"] for row in monitor), default=0)
            max_pnl = max((row["pnl"] for row in monitor), default=0)
            final_pnl = monitor[-1]["pnl"]
        else:
            # Fallback to trade_log data if monitoring data is missing
            start_price = log.get("entry", "N/A")
            end_price = log.get("exit_price", "N/A")
            final_pnl = log.get("pnl", "N/A")
            # We can't know min/max pnl range without tick data, so we imply it from final
            min_pnl = (
                final_pnl
                if isinstance(final_pnl, (int, float)) and final_pnl < 0
                else 0
            )
            max_pnl = (
                final_pnl
                if isinstance(final_pnl, (int, float)) and final_pnl > 0
                else 0
            )

        price_history_context = ""
        if price_history_df is not None and not price_history_df.empty:
            # Create a simplified string representation of the candle data
            # Resample to 5-minute candles if too granular to save tokens
            try:
                # Ensure index is datetime
                if not isinstance(price_history_df.index, pd.DatetimeIndex):
                    price_history_df.index = pd.to_datetime(price_history_df.index)

                # Ensure columns are numeric
                cols = ["open", "high", "low", "close", "volume"]
                for col in cols:
                    if col in price_history_df.columns:
                        price_history_df[col] = pd.to_numeric(
                            price_history_df[col], errors="coerce"
                        )

                # Simple summary statistics
                period_high = price_history_df["high"].max()
                period_low = price_history_df["low"].min()
                period_open = price_history_df["open"].iloc[0]
                period_close = price_history_df["close"].iloc[-1]

                # Candle Data (Last 20 5-min bars)
                agg_dict = {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                }
                if "volume" in price_history_df.columns:
                    agg_dict["volume"] = "sum"

                price_history_context = f"""
        **Broader Market Context (1 Hour before to Present):**
        - Period High: {period_high}
        - Period Low: {period_low}
        - Open: {period_open}
        - Close: {period_close}
        - Candle Data (Last 20 5-min bars):
        {price_history_df.resample("5Min").agg(agg_dict).tail(20).to_string()}
        """
            except Exception as e:
                price_history_context = f"Could not process price history: {e}"

        prompt = f"""
        You are a senior trading risk manager conducting a post-mortem analysis.
        
        **Trade Log:**
        - Entry: {log.get("entry")}
        - Initial Stop Loss: {log.get("initial_stop_loss", "N/A")} (Use this for validation checks)
        - Final Stop Loss: {log.get("stop_loss")} (Might be updated by trailing stop)
        - Take Profit: {log.get("take_profit")}
        - Action: {log.get("action")}
        - Outcome: {log.get("outcome")}
        - Exit Price: {log.get("exit_price")}
        - Reasoning: {log.get("reasoning")}
        
        **Execution Stats:**
        - Spread at Entry: {log.get("spread_at_entry")}
        - Start Price (Bid): {start_price}
        - End Price (Bid): {end_price}
        - PnL Range: {min_pnl} to {max_pnl}
        - Final PnL: {final_pnl}
        
        {price_history_context}
        
        **Monitoring Data Sample (First 5, Last 5):**
        {monitor[:5]}
        ...
        {monitor[-5:]}
        
        **Analysis Required:**
        1. Did the trade follow the plan?
        2. Was the stop loss too tight given the price action?
        3. Did slippage or spread impact the result significantly?
        4. Was the original reasoning sound based on the outcome?
        5. What is the key lesson for next time?
        
        Provide a concise, bulleted report.
        """

        try:
            # Create config with safety settings
            # Using dictionary format which is often supported, or referencing types if needed.
            # For google-genai, usage of types is preferred.

            safety_settings = [
                types.SafetySetting(
                    category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"
                ),
                types.SafetySetting(
                    category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"
                ),
            ]

            config = types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=8192,
                safety_settings=safety_settings,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True,
                    thinking_level="HIGH",
                ),
            )

            response = self.client.models.generate_content(
                model=self.model_name, contents=prompt, config=config
            )

            # Log thoughts if available
            for part in response.candidates[0].content.parts:
                if part.thought:
                    logger.info(
                        f"--- Gemini Post-Mortem Thoughts ---\n{part.text}\n-------------------------------"
                    )

            # Safely access text
            if response.candidates:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    return response.text
                elif candidate.finish_reason == types.FinishReason.SAFETY:  # SAFETY
                    return f"Analysis blocked by safety filters. Ratings: {candidate.safety_ratings}"
                elif (
                    candidate.finish_reason == types.FinishReason.MAX_TOKENS
                ):  # MAX_TOKENS
                    try:
                        return response.text + "\n[TRUNCATED]"
                    except Exception:
                        return "Analysis truncated and text inaccessible."
                else:
                    return f"Analysis finished with reason {candidate.finish_reason} but no text returned."
            else:
                return "No candidates returned from Gemini."

        except Exception as e:
            logger.error(f"Gemini Post-Mortem Error: {e}")
            return "Analysis failed."


if __name__ == "__main__":
    # Simple manual test (requires valid API key in .env)

    # Mock data example

    mock_data = """
    Ticker: FTSE100
    Timeframe: 15 minutes
    Last Price: 7500.0
    ATR (14): 15.0
    RSI (14): 60.0
    Trend (Last 10 candles): Bullish consolidation.
    Support: 7480
    Resistance: 7520
    """

    if GEMINI_API_KEY:
        analyst = GeminiAnalyst()

        result = analyst.analyze_market(mock_data)

        print("Analysis Result:")

        print(result.model_dump_json(indent=2) if result else "Failed")

    else:
        print("Skipping manual test: No GEMINI_API_KEY found.")
