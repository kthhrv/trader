import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Epic to Yahoo Ticker Mapping
EPIC_MAPPING = {
    "IX.D.FTSE.DAILY.IP": "^FTSE",   # FTSE 100
    "IX.D.SPTRD.DAILY.IP": "^GSPC",  # S&P 500
    "IX.D.NIKKEI.DAILY.IP": "NIY=F", # Nikkei 225 Futures
    "CC.D.VIX.USS.IP": "^VIX",       # VIX
    # Add currency pairs or other instruments as needed
    "CS.D.GBPUSD.TODAY.IP": "GBPUSD=X",
    "CS.D.EURUSD.TODAY.IP": "EURUSD=X",
    "CS.D.USDJPY.TODAY.IP": "JPY=X", # Note: Yahoo is usually Quote=X
}

class YFinanceClient:
    def fetch_historical_data_by_range(self, epic: str, resolution: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetches historical OHLC data using yfinance, mimicking IGClient's interface.
        
        Args:
            epic (str): IG Instrument epic.
            resolution (str): IG Resolution string (e.g., '1Min', '5Min', '1H').
            start_date (str): Start datetime (YYYY-MM-DD HH:MM:SS).
            end_date (str): End datetime (YYYY-MM-DD HH:MM:SS).
            
        Returns:
            pd.DataFrame: A DataFrame with 'open', 'high', 'low', 'close', 'volume' columns.
        """
        ticker = EPIC_MAPPING.get(epic)
        if not ticker:
            logger.warning(f"No Yahoo Finance mapping found for epic: {epic}")
            return pd.DataFrame()

        # Map IG resolution to Yahoo resolution
        # IG: 1Min, 5Min, 15Min, 1H, 1D
        # YF: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo
        yf_interval = "1m" # Default
        if resolution == "1Min":
            yf_interval = "1m"
        elif resolution == "5Min":
            yf_interval = "5m"
        elif resolution == "15Min":
            yf_interval = "15m"
        elif resolution == "30Min":
            yf_interval = "30m"
        elif resolution == "1H":
            yf_interval = "1h"
        elif resolution == "DAY" or resolution == "D":
            yf_interval = "1d"
        
        try:
            # yfinance expects YYYY-MM-DD or datetime objects
            # Convert strings to datetime objects to avoid parsing errors
            def parse_date(date_str):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
                raise ValueError(f"Date format not recognized: {date_str}")

            start_dt = parse_date(start_date)
            end_dt = parse_date(end_date)
            
            # yfinance download
            # auto_adjust=True accounts for splits/dividends, which is good for backtesting but 
            # might differ slightly from raw IG prices. Let's keep it False for raw prices if possible, 
            # or True if we want adjusted close. IG gives raw usually. 
            # Using progress=False to reduce clutter.
            
            # Dynamically adjust start_dt for intraday intervals due to yfinance limitations
            if yf_interval in ["1m", "5m", "15m", "30m", "60m", "1h"]:
                max_lookback_dt = end_dt - timedelta(days=7)
                if start_dt < max_lookback_dt:
                    start_dt = max_lookback_dt
                    logger.info(f"Adjusted start_dt for {yf_interval} to {start_dt} due to yfinance lookback limits.")
            
            logger.info(f"YFinance Fetch: {ticker} [{yf_interval}] from {start_dt} to {end_dt}")
            
            df = yf.download(
                ticker, 
                start=start_dt, 
                end=end_dt, 
                interval=yf_interval, 
                progress=False,
                multi_level_index=False,
                auto_adjust=False
            )

            if df.empty:
                logger.warning(f"YFinance returned empty data for {ticker} for interval {yf_interval} from {start_dt} to {end_dt}. This could be due to non-trading hours or data unavailability.")
                return pd.DataFrame()

            # Standardize Columns
            # YFinance returns: Open, High, Low, Close, Adj Close, Volume
            # Rename to lowercase to match IGClient interface
            df.rename(columns={
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            }, inplace=True)
            
            # Ensure index is datetime (it usually is)
            # Filter columns just in case
            cols = ['open', 'high', 'low', 'close', 'volume']
            existing_cols = [c for c in cols if c in df.columns]
            df = df[existing_cols]
            
            return df

        except Exception as e:
            logger.error(f"YFinance fetch failed for {epic} ({ticker}) with interval {yf_interval} and range {start_dt} to {end_dt}: {type(e).__name__}: {e}")
            return pd.DataFrame()
