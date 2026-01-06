import pandas as pd
from src.database import fetch_all_trade_logs


def get_scorecard_data(trades=None):
    """
    Calculates performance metrics and returns them as a dictionary.
    Accepts an optional list of trades; otherwise fetches all logs.
    """
    data = trades if trades is not None else fetch_all_trade_logs()

    if not data:
        return None

    df = pd.DataFrame(data)

    # --- Preprocessing ---
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    def normalize_market(epic):
        if "FTSE" in epic:
            return "LONDON"
        if "DAX" in epic:
            return "GERMANY"
        if "SPTRD" in epic or "US500" in epic:
            return "NEW YORK"
        if "NIKKEI" in epic:
            return "NIKKEI"
        if "ASX" in epic:
            return "AUSTRALIA"
        if "NASDAQ" in epic or "NDX" in epic:
            return "NASDAQ"
        return epic

    df["Market"] = df["epic"].apply(normalize_market)

    total_sessions = len(df)
    ai_waits = len(df[df["outcome"] == "WAIT"])
    ai_errors = len(df[df["outcome"] == "AI_ERROR"])
    rejected = len(df[df["outcome"] == "REJECTED_SAFETY"])

    # Total Trades Taken: Any row that was actually PLACED or CLOSED.
    # We exclude: WAIT, AI_ERROR, REJECTED_SAFETY, PENDING, and TIMED_OUT.
    # Note: TIMED_OUT is a 'stalking event' that never resulted in a trade.
    trade_df = df[
        df["outcome"].str.contains("PLACED")
        | df["outcome"].isin(["WIN", "LOSS", "CLOSED"])
    ]
    total_trades = len(trade_df)

    closed_df = trade_df[trade_df["pnl"].notnull()].copy()

    stats = {
        "total_sessions": total_sessions,
        "ai_waits": ai_waits,
        "rejected": rejected,
        "ai_errors": ai_errors,
        "total_trades": total_trades,
        "net_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "expectancy": 0.0,
        "market_stats": [],
        "conf_stats": [],
    }

    if not closed_df.empty:
        wins = closed_df[closed_df["pnl"] > 0]
        losses = closed_df[closed_df["pnl"] <= 0]

        stats["net_pnl"] = closed_df["pnl"].sum()
        stats["win_rate"] = len(wins) / len(closed_df) * 100

        gross_win = wins["pnl"].sum()
        gross_loss = abs(losses["pnl"].sum())
        stats["profit_factor"] = (
            gross_win / gross_loss if gross_loss > 0 else float("inf")
        )

        avg_win = wins["pnl"].mean() if not wins.empty else 0
        avg_loss = losses["pnl"].mean() if not losses.empty else 0
        stats["expectancy"] = (len(wins) / len(closed_df) * avg_win) + (
            len(losses) / len(closed_df) * avg_loss
        )

        # Market Breakdown
        m_stats = (
            closed_df.groupby("Market")
            .agg(
                Trades=("id", "count"),
                Net_PnL=("pnl", "sum"),
                Win_Rate=("pnl", lambda x: (x > 0).mean() * 100),
            )
            .sort_values(by="Net_PnL", ascending=False)
            .reset_index()
        )
        stats["market_stats"] = m_stats.to_dict("records")

        # AI Confidence Audit
        if "confidence" in closed_df.columns:
            closed_df["confidence"] = closed_df["confidence"].astype(str).str.upper()
            c_stats = (
                closed_df.groupby("confidence")
                .agg(
                    Trades=("id", "count"),
                    Win_Rate=("pnl", lambda x: (x > 0).mean() * 100),
                    Avg_PnL=("pnl", "mean"),
                )
                .reset_index()
            )
            stats["conf_stats"] = c_stats.to_dict("records")

        # Entry Type Audit
        if "entry_type" in closed_df.columns:
            # Normalize entry_type case
            closed_df["entry_type"] = closed_df["entry_type"].astype(str).str.upper()
            e_stats = (
                closed_df.groupby("entry_type")
                .agg(
                    Trades=("id", "count"),
                    Win_Rate=("pnl", lambda x: (x > 0).mean() * 100),
                    Avg_PnL=("pnl", "mean"),
                )
                .reset_index()
            )
            stats["entry_stats"] = e_stats.to_dict("records")

    return stats


def generate_scorecard(trades=None):
    """
    Generates and prints a performance scorecard based on logs.
    Accepts an optional list of trades.
    """
    stats = get_scorecard_data(trades=trades)

    if not stats:
        print("No data found in trade_log.")
        return

    print("\n" + "=" * 60)
    print(f"{'TRADER SCORECARD':^60}")
    print("=" * 60)

    # --- 1. The Funnel ---
    print("\n[ THE FUNNEL ]")
    print(f"Total Sessions:      {stats['total_sessions']}")
    print(
        f"  > AI Waits:        {stats['ai_waits']} ({stats['ai_waits'] / stats['total_sessions'] * 100:.1f}%)"
    )
    print(
        f"  > Safety Rejects:  {stats['rejected']} ({stats['rejected'] / stats['total_sessions'] * 100:.1f}%)"
    )
    print(f"  > AI Errors:       {stats['ai_errors']}")
    print(
        f"Total Trades Taken:  {stats['total_trades']} (Conv: {stats['total_trades'] / stats['total_sessions'] * 100:.1f}%)"
    )

    if not stats["market_stats"]:
        print("\nNo closed trades to analyze yet.")
        return

    print("\n[ PERFORMANCE ]")
    print(f"Net PnL:             £{stats['net_pnl']:+.2f}")
    print(f"Win Rate:            {stats['win_rate']:.1f}%")
    print(f"Profit Factor:       {stats['profit_factor']:.2f}")
    print(f"Expectancy:          £{stats['expectancy']:.2f} per trade")

    # --- 3. Market Breakdown ---
    print("\n[ MARKET LEAGUE TABLE ]")
    df_m = pd.DataFrame(stats["market_stats"])
    print(df_m.to_string(index=False))

    # --- 4. AI Confidence Audit ---
    print("\n[ AI CONFIDENCE AUDIT ]")
    if stats["conf_stats"]:
        df_c = pd.DataFrame(stats["conf_stats"])
        print(df_c.to_string(index=False))
    else:
        print("Confidence data missing.")

    # --- 5. Entry Type Audit ---
    print("\n[ ENTRY TYPE AUDIT ]")
    if "entry_stats" in stats and stats["entry_stats"]:
        df_e = pd.DataFrame(stats["entry_stats"])
        print(df_e.to_string(index=False))
    else:
        print("Entry Type data missing.")

    print("\n" + "=" * 60 + "\n")
