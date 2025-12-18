import pandas as pd
from src.database import fetch_all_trade_logs
import sys

def generate_scorecard():
    """
    Generates and prints a performance scorecard based on all logs in the database.
    """
    data = fetch_all_trade_logs()
    
    if not data:
        print("No data found in trade_log.")
        return

    df = pd.DataFrame(data)
    
    # --- Preprocessing ---
    # Convert timestamps
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Normalize Market Names (e.g., IX.D.FTSE... -> LONDON)
    def normalize_market(epic):
        if "FTSE" in epic: return "LONDON"
        if "DAX" in epic: return "GERMANY"
        if "SPTRD" in epic or "US500" in epic: return "NEW YORK"
        if "NIKKEI" in epic: return "NIKKEI"
        if "ASX" in epic: return "AUSTRALIA"
        if "NASDAQ" in epic or "NDX" in epic: return "NASDAQ"
        return epic
    
    df['Market'] = df['epic'].apply(normalize_market)
    
    # Define "Actionable" vs "Non-Actionable"
    # Actionable: Where a trade was actually PLACED (Live or Dry Run)
    # Non-Actionable: WAIT, REJECTED, TIMED_OUT (if no trade), AI_ERROR
    
    # We look at 'outcome'.
    # Outcomes like 'WIN', 'LOSS', 'LIVE_PLACED', 'DRY_RUN_PLACED' imply action.
    # We can infer 'Filled' if PnL is not None or if outcome implies a closed trade.
    
    # Simplification:
    # 1. Total Sessions = Total Rows
    # 2. AI Signals = Action != WAIT
    # 3. Trades Taken = Deal ID is present OR Outcome indicates placement
    # 4. Trades Closed = PnL is not Null
    
    total_sessions = len(df)
    
    # AI Signal Stats
    ai_waits = len(df[df['outcome'] == 'WAIT'])
    ai_errors = len(df[df['outcome'] == 'AI_ERROR'])
    rejected = len(df[df['outcome'] == 'REJECTED_SAFETY'])
    
    # Trades
    # Filter for rows where a trade was actually attempted/placed
    # (Checking if 'outcome' contains PLACED or if PnL exists)
    trade_df = df[df['outcome'].str.contains('PLACED') | df['pnl'].notnull() | df['outcome'].isin(['WIN', 'LOSS', 'TIMED_OUT'])]
    # Filter out pure TIMED_OUT events where no deal was ever made (synthetic IDs start with TIMEOUT)
    trade_df = trade_df[~trade_df['deal_id'].astype(str).str.startswith('TIMEOUT')]
    
    total_trades = len(trade_df)
    
    # Closed Trades (for PnL stats)
    closed_df = trade_df[trade_df['pnl'].notnull()].copy()
    
    print("\n" + "="*60)
    print(f"{ 'TRADER SCORECARD':^60}")
    print("="*60)
    
    # --- 1. The Funnel ---
    print(f"\n[ THE FUNNEL ]")
    print(f"Total Sessions:      {total_sessions}")
    print(f"  > AI Waits:        {ai_waits} ({ai_waits/total_sessions*100:.1f}%)")
    print(f"  > Safety Rejects:  {rejected} ({rejected/total_sessions*100:.1f}%)")
    print(f"  > AI Errors:       {ai_errors}")
    print(f"Total Trades Taken:  {total_trades} (Conv: {total_trades/total_sessions*100:.1f}%)")
    
    if closed_df.empty:
        print("\nNo closed trades to analyze yet.")
        return

    # --- 2. Executive Summary (PnL) ---
    wins = closed_df[closed_df['pnl'] > 0]
    losses = closed_df[closed_df['pnl'] <= 0]
    
    total_pnl = closed_df['pnl'].sum()
    win_rate = len(wins) / len(closed_df) * 100
    
    gross_win = wins['pnl'].sum()
    gross_loss = abs(losses['pnl'].sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float('inf')
    
    avg_win = wins['pnl'].mean() if not wins.empty else 0
    avg_loss = losses['pnl'].mean() if not losses.empty else 0
    
    # Expectancy = (Win % * Avg Win) - (Loss % * Avg Loss)
    expectancy = (len(wins)/len(closed_df) * avg_win) + (len(losses)/len(closed_df) * avg_loss) # avg_loss is negative

    print(f"\n[ PERFORMANCE ]")
    print(f"Net PnL:             £{total_pnl:+.2f}")
    print(f"Win Rate:            {win_rate:.1f}% ({len(wins)} W / {len(losses)} L)")
    print(f"Profit Factor:       {profit_factor:.2f}")
    print(f"Avg Win / Loss:      £{avg_win:.2f} / £{avg_loss:.2f}")
    print(f"Expectancy:          £{expectancy:.2f} per trade")

    # --- 3. Market Breakdown ---
    print(f"\n[ MARKET LEAGUE TABLE ]")
    market_stats = closed_df.groupby('Market').agg(
        Trades=('id', 'count'),
        Net_PnL=('pnl', 'sum'),
        Win_Rate=('pnl', lambda x: (x > 0).mean() * 100)
    ).sort_values(by='Net_PnL', ascending=False)
    
    # Formatting
    market_stats['Net_PnL'] = market_stats['Net_PnL'].apply(lambda x: f"£{x:+.2f}")
    market_stats['Win_Rate'] = market_stats['Win_Rate'].apply(lambda x: f"{x:.1f}%")
    
    print(market_stats.to_string())

    # --- 4. AI Confidence Audit ---
    print(f"\n[ AI CONFIDENCE AUDIT ]")
    if 'confidence' in closed_df.columns:
        # Normalize confidence case
        closed_df['confidence'] = closed_df['confidence'].astype(str).str.upper()
        
        conf_stats = closed_df.groupby('confidence').agg(
            Trades=('id', 'count'),
            Win_Rate=('pnl', lambda x: (x > 0).mean() * 100),
            Avg_PnL=('pnl', 'mean')
        )
        conf_stats['Win_Rate'] = conf_stats['Win_Rate'].apply(lambda x: f"{x:.1f}%")
        conf_stats['Avg_PnL'] = conf_stats['Avg_PnL'].apply(lambda x: f"£{x:+.2f}")
        
        print(conf_stats.to_string())
    else:
        print("Confidence data missing.")

    # --- 5. Entry Type Audit ---
    print(f"\n[ ENTRY TYPE AUDIT ]")
    if 'entry_type' in closed_df.columns:
        # Normalize entry_type case
        closed_df['entry_type'] = closed_df['entry_type'].astype(str).str.upper()
        
        entry_stats = closed_df.groupby('entry_type').agg(
            Trades=('id', 'count'),
            Win_Rate=('pnl', lambda x: (x > 0).mean() * 100),
            Avg_PnL=('pnl', 'mean')
        )
        entry_stats['Win_Rate'] = entry_stats['Win_Rate'].apply(lambda x: f"{x:.1f}%")
        entry_stats['Avg_PnL'] = entry_stats['Avg_PnL'].apply(lambda x: f"£{x:+.2f}")
        
        print(entry_stats.to_string())
    else:
        print("Entry Type data missing.")

    print("\n" + "="*60 + "\n")
