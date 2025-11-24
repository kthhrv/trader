import os
import google.generativeai as genai
import typing_extensions as typing
from pydantic import BaseModel, Field
from enum import Enum
import json
from config import GEMINI_API_KEY

# Configure the SDK
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

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
    confidence: str = Field(description="Confidence level of the analysis (e.g., 'high', 'medium', 'low').")
    reasoning: str = Field(description="Brief explanation of the trade rationale based on technicals.")

class GeminiAnalyst:
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        """
        Initializes the Gemini Analyst with a specific model.
        Using gemini-2.5-flash as it is fast and supports structured output well.
        """
        self.model_name = model_name
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction="""
            You are an expert financial trading analyst specializing in breakout strategies for market opens (London/NY).
            Your goal is to analyze provided OHLC market data, technical indicators, and news sentiment to generate high-quality trading triggers.
            
            STRICT RULES:
            1. Always analyze the risk/reward ratio.
            2. Ensure Stop Loss is logical based on recent support/resistance.
            3. If the market conditions are choppy or unclear, recommend 'WAIT'.
            4. Output MUST be strictly in the requested JSON format.
            """
        )

    def analyze_market(self, market_data_context: str) -> typing.Optional[TradingSignal]:
        """
        Sends market data to Gemini and returns a structured TradingSignal.
        
        Args:
            market_data_context (str): A string containing OHLC data, technical indicators (ATR, RSI, etc.), 
                                       and any relevant news context.
        
        Returns:
            TradingSignal: A Pydantic object containing the trade plan, or None if generation fails.
        """
        try:
            # Enforce structured output by passing the Pydantic class to response_schema
            response = self.model.generate_content(
                f"Analyze the following market data and provide a trading signal:\n\n{market_data_context}",
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=TradingSignal
                )
            )
            
            # The SDK with response_schema automatically handles the schema enforcement, 
            # but we parse it back into our Pydantic model for type safety in the app.
            signal_data = json.loads(response.text)
            return TradingSignal(**signal_data)

        except Exception as e:
            print(f"Error during Gemini analysis: {e}")
            return None

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
