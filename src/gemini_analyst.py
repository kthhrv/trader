import os
import google.generativeai as genai
import typing_extensions as typing
from pydantic import BaseModel, Field
from enum import Enum
import json
import pandas as pd
from config import GEMINI_API_KEY

# Configure the SDK
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

class EntryType(str, Enum):
    INSTANT = "INSTANT"
    CONFIRMATION = "CONFIRMATION"

class TradingSignal(BaseModel):

    ticker: str = Field(description="The ticker symbol of the asset analyzed.")

    action: Action = Field(description="The trading action recommendation.")

    entry: float = Field(description="The suggested entry price level.")
    
    entry_type: EntryType = Field(description="The type of entry: 'INSTANT' (execute immediately when price touches level) or 'CONFIRMATION' (wait for 1-minute candle close beyond level). Default to INSTANT for high momentum, CONFIRMATION for risky setups.")

    stop_loss: float = Field(description="The stop loss price level.")

    take_profit: float = Field(description="The take profit price level.")

    size: float = Field(description="The suggested trade size per point.")

    atr: float = Field(description="The Average True Range (ATR) at the time of analysis.")

    use_trailing_stop: bool = Field(description="Whether to use a dynamic trailing stop (True) or a fixed take profit (False). Set to True for breakout/trend strategies to maximize runs. Set to False for range/mean-reversion strategies where price is expected to reverse at target.")

    confidence: str = Field(description="Confidence level of the analysis (e.g., 'high', 'medium', 'low').")

    reasoning: str = Field(description="Brief explanation of the trade rationale based on technicals.")



class GeminiAnalyst:

    def __init__(self, model_name: str = "gemini-3-pro-preview"):

        """
        Initializes the Gemini Analyst with a Vertex AI model, but using the google-generativeai SDK.
        """

        self.model_name = model_name

        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction="""
            You are an expert financial trading analyst specializing in breakout strategies for market opens (London/Nikkei).
            Your goal is to analyze provided OHLC market data, technical indicators, and news sentiment to generate high-quality trading triggers.
            
            STRICT RULES:
            1. Always analyze the risk/reward ratio. Ensure Stop Loss is logical based on recent support/resistance and **AT LEAST 1.0x ATR** from the entry price, with a minimum of 10 POINTS.
            2. If the market conditions are choppy, low liquidity, or unclear (e.g., conflicting signals), recommend 'WAIT'.
            3. **Entry Type Strategy:**
               - Select **'INSTANT'** if momentum is strong and you want to catch a fast breakout immediately upon touching the level.
               - Select **'CONFIRMATION'** if the level is major support/resistance and there is a risk of a "fakeout". This tells the bot to wait for a 1-minute candle CLOSE beyond the level before entering.
            4. **Trailing Stop Strategy:**
               - Set **'use_trailing_stop' = True** if the setup is a high-momentum breakout where price could run significantly (Trend Following).
               - Set **'use_trailing_stop' = False** if the setup is targeting a specific resistance level or trading inside a range (Mean Reversion), where a fixed Take Profit is better.
            5. Your output MUST follow a Chain-of-Thought process BEFORE the JSON, like this:
               *   **Market Overview:** Summarize the current trend, volatility (ATR), and momentum (RSI).
               *   **Key Levels:** Identify significant support and resistance levels from the OHLC data.
               *   **News Sentiment:** Evaluate the overall sentiment from the provided news headlines (Positive, Negative, Neutral).
               *   **Trade Rationale:** Based on the above, explain WHY a BUY/SELL/WAIT signal is generated. Justify entry, stop loss, take profit, trade size, and why the **ATR-based stop** is appropriate. Explicitly justify the choice of 'INSTANT' vs 'CONFIRMATION' entry AND 'use_trailing_stop'. Ensure Stop Loss is NOT placed *within* the range of the opening 5-minute candle; instead, aim for structural lows (e.g., below the 08:00 low for a BUY).
               *   **Risk/Reward:** Briefly state the estimated risk/reward for the proposed trade.
            6. After the Chain-of-Thought, your final output MUST be strictly in the requested JSON format, and ONLY the JSON. Ensure the 'atr' field reflects the current ATR value provided in the market context.
            """
        )



    def analyze_market(self, market_data_context: str, strategy_name: str = "Market Open") -> typing.Optional[TradingSignal]:

        """
        Sends market data to Gemini and returns a structured TradingSignal.
        """
        try:
            prompt = f"Develop a trading strategy for the {strategy_name} based on the following market data, and provide a trading signal:\n\n{market_data_context}"
            
            response = self.model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=TradingSignal
                )
            )
            
            # The SDK with response_schema automatically handles the schema enforcement
            signal_data = json.loads(response.text)
            # Handle potential missing entry_type from older models or if omitted (default fallback)
            if 'entry_type' not in signal_data:
                signal_data['entry_type'] = EntryType.INSTANT
            if 'use_trailing_stop' not in signal_data:
                signal_data['use_trailing_stop'] = True # Default to True (original behavior)
            return TradingSignal(**signal_data)

        except Exception as e:
            print(f"Error during Gemini analysis: {e}")
            return None

    def generate_post_mortem(self, trade_data: dict, price_history_df: pd.DataFrame = None) -> str:
        """
        Generates a post-mortem analysis for a completed trade.
        """
        log = trade_data.get('log', {})
        monitor = trade_data.get('monitor', [])
        
        # Summarize monitoring data with fallbacks from log
        if monitor:
            start_price = monitor[0]['bid']
            end_price = monitor[-1]['bid']
            min_pnl = min((row['pnl'] for row in monitor), default=0)
            max_pnl = max((row['pnl'] for row in monitor), default=0)
            final_pnl = monitor[-1]['pnl']
        else:
            # Fallback to trade_log data if monitoring data is missing
            start_price = log.get('entry', "N/A")
            end_price = log.get('exit_price', "N/A")
            final_pnl = log.get('pnl', "N/A")
            # We can't know min/max pnl range without tick data, so we imply it from final
            min_pnl = final_pnl if isinstance(final_pnl, (int, float)) and final_pnl < 0 else 0
            max_pnl = final_pnl if isinstance(final_pnl, (int, float)) and final_pnl > 0 else 0

        price_history_context = ""
        if price_history_df is not None and not price_history_df.empty:
            # Create a simplified string representation of the candle data
            # Resample to 5-minute candles if too granular to save tokens
            try:
                # Ensure index is datetime
                if not isinstance(price_history_df.index, pd.DatetimeIndex):
                    price_history_df.index = pd.to_datetime(price_history_df.index)
                
                # Simple summary statistics
                period_high = price_history_df['high'].max()
                period_low = price_history_df['low'].min()
                period_open = price_history_df['open'].iloc[0]
                period_close = price_history_df['close'].iloc[-1]
                
                price_history_context = f"""
        **Broader Market Context (1H before to Present):**
        - Period High: {period_high}
        - Period Low: {period_low}
        - Open: {period_open}
        - Close: {period_close}
        - Candle Data (Last 20 5-min bars):
        {price_history_df.resample('5Min').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last'}).tail(20).to_string()}
        """
            except Exception as e:
                price_history_context = f"Could not process price history: {e}"

        prompt = f"""
        You are a senior trading risk manager conducting a post-mortem analysis.
        
        **Trade Plan:**
        - Ticker: {log.get('epic')}
        - Action: {log.get('action')}
        - Planned Entry: {log.get('entry')}
        - Planned Stop: {log.get('stop_loss')}
        - Planned TP: {log.get('take_profit')}
        - Reasoning: {log.get('reasoning')}
        
        **Execution & Outcome:**
        - Outcome: {log.get('outcome')}
        - Spread at Entry: {log.get('spread_at_entry')}
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
            # Create a temporary config for text
            text_config = genai.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192 # Increased token limit to prevent truncation
            )
            
            # Permissive safety settings for financial analysis
            safety_settings = [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE",
                },
            ]
            
            response = self.model.generate_content(
                prompt, 
                generation_config=text_config,
                safety_settings=safety_settings
            )
            
            # Safely access text
            if response.candidates:
                candidate = response.candidates[0]
                if candidate.content.parts:
                    return response.text
                elif candidate.finish_reason == 3: # SAFETY
                    return f"Analysis blocked by safety filters. Ratings: {candidate.safety_ratings}"
                elif candidate.finish_reason == 2: # MAX_TOKENS
                    try:
                        return response.text + "\n[TRUNCATED]"
                    except:
                        return "Analysis truncated and text inaccessible."
                else:
                    return f"Analysis finished with reason {candidate.finish_reason} but no text returned."
            else:
                return "No candidates returned from Gemini."
            
        except Exception as e:
            print(f"Gemini Post-Mortem Error: {e}")
            return "Analysis failed."

if __name__ == "__main__":

    # Simple manual test (requires valid API key in .env)

    # Mock data example

    mock_data = """
    Ticker: FTSE100
    Timeframe: 15min
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
