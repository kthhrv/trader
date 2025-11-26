import os
import google.generativeai as genai
import typing_extensions as typing
from pydantic import BaseModel, Field
from enum import Enum
import json

# Configure the SDK to use Vertex AI as the backend
# This uses your Application Default Credentials and project for billing.
try:
    genai.configure(
        transport='vertex_ai',
        project='keith-gemini-cli-test-01',
    )
except Exception as e:
    print(f"Failed to configure Gemini with Vertex AI transport: {e}")


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

class TradingSignal(BaseModel):

    ticker: str = Field(description="The ticker symbol of the asset analyzed.")

    action: Action = Field(description="The trading action recommendation.")

    entry: float = Field(description="The suggested entry price level.")

    stop_loss: float = Field(description="The stop loss price level.")

    take_profit: float = Field(description="The take profit price level.")

    size: float = Field(description="The suggested trade size per point.")

    atr: float = Field(description="The Average True Range (ATR) at the time of analysis.")

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
            You are an expert financial trading analyst specializing in breakout strategies for market opens (London/NY/Nikkei).
            Your goal is to analyze provided OHLC market data, technical indicators, and news sentiment to generate high-quality trading triggers.
            
            STRICT RULES:
            1. Always analyze the risk/reward ratio. Ensure Stop Loss is logical based on recent support/resistance and **AT LEAST 1.0x ATR** from the entry price, with a minimum of 10 POINTS.
            2. If the market conditions are choppy, low liquidity, or unclear (e.g., conflicting signals), recommend 'WAIT'.
            3. Your output MUST follow a Chain-of-Thought process BEFORE the JSON, like this:
               *   **Market Overview:** Summarize the current trend, volatility (ATR), and momentum (RSI).
               *   **Key Levels:** Identify significant support and resistance levels from the OHLC data.
               *   **News Sentiment:** Evaluate the overall sentiment from the provided news headlines (Positive, Negative, Neutral).
               *   **Trade Rationale:** Based on the above, explain WHY a BUY/SELL/WAIT signal is generated. Justify entry, stop loss, take profit, trade size, and why the **ATR-based stop** is appropriate.
               *   **Risk/Reward:** Briefly state the estimated risk/reward for the proposed trade.
            4. After the Chain-of-Thought, your final output MUST be strictly in the requested JSON format, and ONLY the JSON. Ensure the 'atr' field reflects the current ATR value provided in the market context.
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
            return TradingSignal(**signal_data)

        except Exception as e:
            print(f"Error during Gemini analysis: {e}")
            return None

    def generate_post_mortem(self, trade_data: dict) -> str:
        """
        Generates a post-mortem analysis for a completed trade.
        """
        log = trade_data.get('log', {})
        monitor = trade_data.get('monitor', [])
        
        # Summarize monitoring data
        start_price = monitor[0]['bid'] if monitor else "N/A"
        end_price = monitor[-1]['bid'] if monitor else "N/A"
        min_pnl = min((row['pnl'] for row in monitor), default=0)
        max_pnl = max((row['pnl'] for row in monitor), default=0)
        final_pnl = monitor[-1]['pnl'] if monitor else "N/A"
        
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
                max_output_tokens=2000 # Increased token limit
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

    analyst = GeminiAnalyst()
    result = analyst.analyze_market(mock_data)
    print("Analysis Result:")
    print(result.model_dump_json(indent=2) if result else "Failed")
