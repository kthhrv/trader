import reflex as rx
import sys
import os
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go  # Import Plotly

# Add parent directory to path to import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from src.database import fetch_candles_range, fetch_trades_in_range
from src.market_status import MarketStatus

# IGClient removed
from src.scorecard import get_scorecard_data


class State(rx.State):
    """The app state."""

    trades: list[dict] = []
    pnl_history: list[dict] = []

    # Date Filter State
    start_date: str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date: str = datetime.now().strftime("%Y-%m-%d")

    # Market Status
    uk_status: str = "Checking..."
    us_status: str = "Checking..."
    jp_status: str = "Checking..."
    de_status: str = "Checking..."
    au_status: str = "Checking..."
    ndx_status: str = "Checking..."

    # Scorecard Stats
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    net_pnl: float = 0.0
    conversion_rate: float = 0.0
    market_breakdown: list[dict] = []

    last_updated: str = ""

    # Trade Detail & Graph State
    selected_trade: dict = {}
    chart_figure: go.Figure = go.Figure()  # Plotly Figure State
    show_detail: bool = False
    is_loading_graph: bool = False
    graph_cache: dict = {}
    is_fullscreen: bool = False

    class Config:
        arbitrary_types_allowed = True

    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen

    def set_start(self, val: str):
        self.start_date = val
        self.load_data()

    def set_end(self, val: str):
        self.end_date = val
        self.load_data()

    def load_data(self):
        """Fetch data from the database and update market status."""
        try:
            # Use date range
            start_iso = f"{self.start_date}T00:00:00"
            end_iso = f"{self.end_date}T23:59:59"

            raw_trades = fetch_trades_in_range(start_iso, end_iso)

            # Process trades for Table (Keep original order: newest first)
            processed_trades = []
            for t in raw_trades:
                processed_t = {k: (v if v is not None else "") for k, v in t.items()}
                processed_trades.append(processed_t)
            self.trades = processed_trades

            # Process trades for Graph (Sort Oldest -> Newest)
            valid_trades = [
                t
                for t in raw_trades
                if t.get("pnl") is not None and isinstance(t.get("pnl"), (int, float))
            ]
            valid_trades.sort(key=lambda x: x["timestamp"])

            cumulative_pnl = 0.0
            history = []
            for t in valid_trades:
                cumulative_pnl += t["pnl"]
                history.append(
                    {
                        "date": t["timestamp"],
                        "pnl": t["pnl"],
                        "cumulative_pnl": round(cumulative_pnl, 2),
                    }
                )
            self.pnl_history = history

            # Load Scorecard Data (Filtered)
            stats = get_scorecard_data(trades=raw_trades)
            if stats:
                self.win_rate = round(stats["win_rate"], 1)
                self.profit_factor = round(stats["profit_factor"], 2)
                self.total_trades = stats["total_trades"]
                self.net_pnl = round(stats["net_pnl"], 2)
                if stats["total_sessions"] > 0:
                    self.conversion_rate = round(
                        (stats["total_trades"] / stats["total_sessions"]) * 100, 1
                    )
                self.market_breakdown = stats["market_stats"]

        except Exception as e:
            print(f"Error fetching data: {e}")
            self.trades = []
            self.pnl_history = []

        # Check Market Status
        ms = MarketStatus()
        self.uk_status = ms.get_market_status("IX.D.FTSE.DAILY.IP")
        self.us_status = ms.get_market_status("IX.D.SPTRD.DAILY.IP")
        self.jp_status = ms.get_market_status("IX.D.NIKKEI.DAILY.IP")
        self.de_status = ms.get_market_status("IX.D.DAX.DAILY.IP")
        self.au_status = ms.get_market_status("IX.D.ASX.MONTH1.IP")
        self.ndx_status = ms.get_market_status("IX.D.NASDAQ.CASH.IP")

        self.last_updated = datetime.now().strftime("%H:%M:%S")

    def open_trade_detail(self, trade: dict):
        """Sets the selected trade and fetches historical data for the graph."""
        self.selected_trade = trade
        self.show_detail = True
        self.is_loading_graph = True
        self.chart_figure = go.Figure()  # Reset figure

        try:
            # Parse timestamps
            entry_ts_str = trade.get("timestamp")
            exit_ts_str = trade.get("exit_time")

            if not entry_ts_str:
                self.is_loading_graph = False
                return

            entry_dt = datetime.fromisoformat(entry_ts_str)

            if exit_ts_str and exit_ts_str != "":
                try:
                    exit_dt = datetime.fromisoformat(exit_ts_str)
                except ValueError:
                    exit_dt = datetime.now()
            else:
                exit_dt = datetime.now()

            # Safety check: If Exit is before Entry (corrupted data), default to Now
            if exit_dt <= entry_dt:
                print(
                    f"WARN: Exit time {exit_dt} <= Entry time {entry_dt}. Defaulting exit to Now."
                )
                exit_dt = datetime.now()

            # Define Window: Entry - 3h to Exit + 30m
            start_dt = entry_dt - timedelta(hours=3)
            end_dt = exit_dt + timedelta(minutes=30)

            epic = trade.get("epic")

            # Determine resolution for resampling
            duration = end_dt - start_dt
            if duration > timedelta(days=3):
                resample_rule = "1H"
            elif duration > timedelta(hours=24):
                resample_rule = "5T"
            else:
                resample_rule = "1T"  # 1 minute resolution for typical trade view

            # Fetch from DB (1-minute candles)
            print(f"DEBUG: Fetching DB candles for {epic} from {start_dt} to {end_dt}")
            candles = fetch_candles_range(
                epic, start_dt.isoformat(), end_dt.isoformat()
            )

            df = pd.DataFrame()
            if candles:
                df = pd.DataFrame(candles)
                # Ensure columns are correct types
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df.set_index("timestamp", inplace=True)

                    # Resample
                    agg_dict = {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                    }
                    if "volume" in df.columns:
                        agg_dict["volume"] = "sum"

                    # Resample and drop NaNs (empty bins)
                    df_resampled = df.resample(resample_rule).agg(agg_dict).dropna()
                    df = df_resampled.reset_index()

            if df.empty:
                print(
                    f"No graph data found in DB for {epic} (Range: {start_dt} - {end_dt})"
                )
                self.is_loading_graph = False
                return

            # Proceed with Plotting
            date_col = df.columns[0]  # Should be 'timestamp'

            # --- Plotly Figure Construction ---
            fig = go.Figure(
                data=[
                    go.Candlestick(
                        x=df[date_col],
                        open=df["open"],
                        high=df["high"],
                        low=df["low"],
                        close=df["close"],
                        name=epic,
                    )
                ]
            )

            # Add Trend Line (SMA 10)
            fig.add_trace(
                go.Scatter(
                    x=df[date_col],
                    y=df["close"].rolling(window=10).mean(),
                    mode="lines",
                    name="Trend (SMA 10)",
                    line=dict(color="yellow", width=1.5),
                )
            )

            # Add Bollinger Bands
            sma_20 = df["close"].rolling(window=20).mean()
            std_20 = df["close"].rolling(window=20).std()
            upper_bb = sma_20 + (std_20 * 2)
            lower_bb = sma_20 - (std_20 * 2)

            fig.add_trace(
                go.Scatter(
                    x=df[date_col],
                    y=upper_bb,
                    line=dict(color="rgba(255, 255, 255, 0.3)", width=1, dash="dash"),
                    name="Upper BB",
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=df[date_col],
                    y=lower_bb,
                    line=dict(color="rgba(255, 255, 255, 0.3)", width=1, dash="dash"),
                    fill="tonexty",  # Fill area between Upper and Lower
                    fillcolor="rgba(255, 255, 255, 0.05)",
                    name="Lower BB",
                )
            )

            # Add ATR Bands (1.5x)
            try:
                # Calculate ATR using pandas_ta
                df.ta.atr(length=14, append=True)
                # The column name generated is usually ATRe_14 or similar, finding it dynamically
                atr_col = [c for c in df.columns if "ATR" in c][0]
                atr = df[atr_col]

                upper_atr = sma_20 + (atr * 1.5)
                lower_atr = sma_20 - (atr * 1.5)

                fig.add_trace(
                    go.Scatter(
                        x=df[date_col],
                        y=upper_atr,
                        line=dict(color="cyan", width=1, dash="dot"),
                        name="Upper ATR (1.5x)",
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=df[date_col],
                        y=lower_atr,
                        line=dict(color="cyan", width=1, dash="dot"),
                        name="Lower ATR (1.5x)",
                    )
                )
            except Exception as e:
                print(f"Could not add ATR bands: {e}")

            # Add Horizontal Lines (Entry, SL, TP)
            entry = float(trade.get("entry", 0))
            sl = float(trade.get("stop_loss", 0))
            init_sl = float(trade.get("initial_stop_loss", 0) or 0)
            tp = float(trade.get("take_profit", 0) or 0)  # Handle None

            if entry > 0:
                fig.add_hline(
                    y=entry,
                    line_dash="dash",
                    line_color="green",
                    annotation_text="Entry",
                )
            if sl > 0:
                fig.add_hline(
                    y=sl,
                    line_dash="dash",
                    line_color="orange",
                    annotation_text="Curr SL",
                )
            if init_sl > 0 and init_sl != sl:
                fig.add_hline(
                    y=init_sl,
                    line_dash="dot",
                    line_color="darkorange",
                    annotation_text="Init SL",
                )
            if tp > 0:
                fig.add_hline(
                    y=tp, line_dash="dash", line_color="purple", annotation_text="TP"
                )

            # Add Trailing Stop Trigger (1.5R)
            if entry > 0 and sl > 0:
                risk = abs(entry - sl)
                action = trade.get("action", "").upper()
                trigger_price = 0

                if action == "BUY":
                    trigger_price = entry + (1.5 * risk)
                elif action == "SELL":
                    trigger_price = entry - (1.5 * risk)

                if trigger_price > 0:
                    fig.add_hline(
                        y=trigger_price,
                        line_dash="dot",
                        line_color="cyan",
                        annotation_text="Trail Trigger (1.5R)",
                        annotation_position="top left",
                    )

            # Add Vertical Lines (Entry Time, Exit Time)
            # Plotly expects datetime objects or strings matching x-axis
            fig.add_vline(x=entry_dt, line_dash="dot", line_color="green")
            fig.add_vline(x=exit_dt, line_dash="dot", line_color="red")

            # Layout Styling for Dark Mode
            fig.update_layout(
                template="plotly_dark",
                margin=dict(l=20, r=20, t=20, b=20),
                xaxis_rangeslider_visible=False,
                height=400,
                paper_bgcolor="rgba(0,0,0,0)",  # Transparent background
                plot_bgcolor="rgba(0,0,0,0)",
            )

            self.chart_figure = fig

        except Exception as e:
            print(f"Error building graph: {e}")

        self.is_loading_graph = False

    def show_demo_chart(self):
        """Generates a synthetic demo trade and chart for visualization."""
        self.show_detail = True
        self.is_loading_graph = True
        self.chart_figure = go.Figure()

        # 1. Create Mock Trade
        now = datetime.now()
        entry_time = now - timedelta(hours=2)
        exit_time = now - timedelta(minutes=30)

        mock_trade = {
            "deal_id": "DEMO_123",
            "epic": "DEMO.FTSE100",
            "action": "BUY",
            "entry": 7500,
            "exit_price": 7535,  # Added exit price
            "initial_stop_loss": 7480,
            "stop_loss": 7495,  # Moved up
            "take_profit": 7540,
            "outcome": "WIN",
            "pnl": 200.0,
            "reasoning": "DEMO: Bullish breakout pattern on 5m timeframe.",
            "timestamp": entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "entry_time_graph": entry_time.strftime("%H:%M"),
            "exit_time_graph": exit_time.strftime("%H:%M"),
            "trailing_activation_price": 7530,  # Example trailing start
            "graph_y_min": 7470,
            "graph_y_max": 7560,
        }
        self.selected_trade = mock_trade

        # 2. Generate Synthetic OHLC Data
        import random

        data = []
        current_price = 7490  # Start below entry
        current_time = entry_time - timedelta(minutes=60)

        for i in range(40):  # 40 candles of 5 mins = 3h 20m
            # Random walk
            change = random.uniform(-3, 5)
            open_p = current_price
            close_p = current_price + change
            high_p = max(open_p, close_p) + random.uniform(0, 2)
            low_p = min(open_p, close_p) - random.uniform(0, 2)

            data.append(
                {
                    "time": current_time,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                }
            )
            current_price = close_p
            current_time += timedelta(minutes=5)

        df = pd.DataFrame(data)

        # 3. Build Plotly Figure
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=df["time"],
                    open=df["open"],
                    high=df["high"],
                    low=df["low"],
                    close=df["close"],
                    name="DEMO.FTSE100",
                )
            ]
        )

        # Add Trend Line (SMA 10)
        fig.add_trace(
            go.Scatter(
                x=df["time"],
                y=df["close"].rolling(window=10).mean(),
                mode="lines",
                name="Trend (SMA 10)",
                line=dict(color="yellow", width=1.5),
            )
        )

        # Add Bollinger Bands
        sma_20 = df["close"].rolling(window=20).mean()
        std_20 = df["close"].rolling(window=20).std()
        upper_bb = sma_20 + (std_20 * 2)
        lower_bb = sma_20 - (std_20 * 2)

        fig.add_trace(
            go.Scatter(
                x=df["time"],
                y=upper_bb,
                line=dict(color="rgba(255, 255, 255, 0.3)", width=1, dash="dash"),
                name="Upper BB",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=df["time"],
                y=lower_bb,
                line=dict(color="rgba(255, 255, 255, 0.3)", width=1, dash="dash"),
                fill="tonexty",
                fillcolor="rgba(255, 255, 255, 0.05)",
                name="Lower BB",
            )
        )

        # Add ATR Bands (1.5x)
        try:
            df.ta.atr(length=14, append=True)
            atr_col = [c for c in df.columns if "ATR" in c][0]
            atr = df[atr_col]

            upper_atr = sma_20 + (atr * 1.5)
            lower_atr = sma_20 - (atr * 1.5)

            fig.add_trace(
                go.Scatter(
                    x=df["time"],
                    y=upper_atr,
                    line=dict(color="cyan", width=1, dash="dot"),
                    name="Upper ATR (1.5x)",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=df["time"],
                    y=lower_atr,
                    line=dict(color="cyan", width=1, dash="dot"),
                    name="Lower ATR (1.5x)",
                )
            )
        except Exception as e:
            print(f"Could not add ATR bands to demo: {e}")

        # Markers
        fig.add_hline(
            y=mock_trade["entry"],
            line_dash="dash",
            line_color="green",
            annotation_text="Entry (7500)",
        )
        fig.add_hline(
            y=mock_trade["stop_loss"],
            line_dash="dash",
            line_color="orange",
            annotation_text=f"Curr SL ({mock_trade['stop_loss']})",
        )
        if mock_trade["initial_stop_loss"] != mock_trade["stop_loss"]:
            fig.add_hline(
                y=mock_trade["initial_stop_loss"],
                line_dash="dot",
                line_color="darkorange",
                annotation_text=f"Init SL ({mock_trade['initial_stop_loss']})",
            )
        fig.add_hline(
            y=mock_trade["take_profit"],
            line_dash="dash",
            line_color="purple",
            annotation_text="TP (7540)",
        )

        # Add Trailing Stop Trigger (1.5R)
        risk = abs(mock_trade["entry"] - mock_trade["stop_loss"])
        trigger_price = (
            mock_trade["entry"] + (1.5 * risk)
            if mock_trade["action"] == "BUY"
            else mock_trade["entry"] - (1.5 * risk)
        )

        fig.add_hline(
            y=trigger_price,
            line_dash="dot",
            line_color="cyan",
            annotation_text="Trail Trigger (1.5R)",
            annotation_position="top left",
        )

        # Middle Line (between Entry and TP)
        mid_line = (mock_trade["entry"] + mock_trade["take_profit"]) / 2
        fig.add_hline(
            y=mid_line,
            line_dash="dot",
            line_color="white",
            annotation_text=f"Mid ({mid_line})",
        )

        fig.add_vline(x=entry_time, line_dash="dot", line_color="green")
        fig.add_vline(x=exit_time, line_dash="dot", line_color="red")

        # Layout
        fig.update_layout(
            template="plotly_dark",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_rangeslider_visible=False,
            height=400,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )

        self.chart_figure = fig
        self.is_loading_graph = False

    def close_detail(self):
        self.show_detail = False
        self.is_fullscreen = False


def status_badge(label: str, status: str) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(label, font_size="0.8em", font_weight="bold"),
            rx.text(
                status,
                color=rx.cond(status.to_string().contains("CLOSED"), "red", "green"),
                font_weight="bold",
            ),
        ),
        padding="1em",
    )


def trade_detail_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            # Header Row
            rx.hstack(
                rx.dialog.title("Trade Analysis"),
                rx.spacer(),
                rx.button(
                    rx.cond(
                        State.is_fullscreen,
                        rx.icon(tag="minimize-2"),
                        rx.icon(tag="maximize-2"),
                    ),
                    on_click=State.toggle_fullscreen,
                    variant="ghost",
                    size="2",
                ),
                rx.dialog.close(
                    rx.button(
                        rx.icon(tag="x"),
                        variant="ghost",
                        size="2",
                        on_click=State.close_detail,
                    ),
                ),
                width="100%",
                align_items="center",
                margin_bottom="1em",
            ),
            rx.dialog.description("Review trade execution and market context."),
            rx.vstack(
                rx.hstack(
                    rx.badge(State.selected_trade["epic"], size="3"),
                    rx.badge(
                        State.selected_trade["action"],
                        color_scheme=rx.cond(
                            State.selected_trade["action"] == "BUY", "green", "red"
                        ),
                        size="3",
                    ),
                    spacing="2",
                    margin_bottom="1em",
                ),
                # Use a grid for better layout of trade details
                rx.grid(
                    rx.text("Outcome:", font_weight="bold", text_align="right"),
                    rx.text(State.selected_trade["outcome"], text_align="left"),
                    rx.text("PnL:", font_weight="bold", text_align="right"),
                    rx.text(State.selected_trade["pnl"], text_align="left"),
                    rx.text("Initial SL:", font_weight="bold", text_align="right"),
                    rx.text(
                        State.selected_trade["initial_stop_loss"], text_align="left"
                    ),
                    rx.text("Current SL:", font_weight="bold", text_align="right"),
                    rx.text(State.selected_trade["stop_loss"], text_align="left"),
                    rx.text("Entry:", font_weight="bold", text_align="right"),
                    rx.text(
                        f"{State.selected_trade['entry']} | Exit: {State.selected_trade['exit_price']}",
                        text_align="left",
                    ),
                    columns="2",
                    spacing_x="4",
                    spacing_y="2",
                    width="100%",
                    max_width="500px",
                    margin_bottom="1em",
                ),
                # Reasoning section
                rx.vstack(
                    rx.text("Reasoning:", font_weight="bold"),
                    rx.text(State.selected_trade["reasoning"], size="1", color="gray"),
                    align_items="flex-start",
                    width="100%",
                    margin_bottom="1em",
                ),
                rx.divider(width="100%"),
                rx.cond(
                    State.is_loading_graph,
                    rx.spinner(),
                    # Use rx.plotly for the chart
                    rx.plotly(
                        data=State.chart_figure,
                        height=rx.cond(State.is_fullscreen, "70vh", "400px"),
                        width="100%",
                    ),
                ),
                spacing="4",
                align_items="flex-start",
                width="100%",
            ),
            rx.flex(
                rx.dialog.close(
                    rx.button("Close", on_click=State.close_detail),
                ),
                justify="end",
                margin_top="1em",
            ),
            max_width=rx.cond(State.is_fullscreen, "100vw", "90vw"),
            width=rx.cond(State.is_fullscreen, "100vw", "auto"),
            height=rx.cond(State.is_fullscreen, "100vh", "auto"),
            padding="2em",
        ),
        open=State.show_detail,
        on_open_change=State.close_detail,
    )


def index() -> rx.Component:
    return rx.box(
        trade_detail_modal(),
        rx.vstack(
            rx.heading("Gemini Trader Bot", size="8"),
            rx.grid(
                status_badge("London", State.uk_status),
                status_badge("Germany", State.de_status),
                status_badge("Nikkei", State.jp_status),
                status_badge("NY (S&P)", State.us_status),
                status_badge("US Tech", State.ndx_status),
                status_badge("Australia", State.au_status),
                columns="6",
                spacing="2",
                width="100%",
            ),
            rx.hstack(
                rx.text("From:", font_weight="bold"),
                rx.input(
                    type="date",
                    value=State.start_date,
                    on_change=State.set_start,
                    width="150px",
                ),
                rx.text("To:", font_weight="bold"),
                rx.input(
                    type="date",
                    value=State.end_date,
                    on_change=State.set_end,
                    width="150px",
                ),
                rx.spacer(),
                rx.text(f"Last Updated: {State.last_updated}", color="gray"),
                rx.button("Refresh", on_click=State.load_data),
                rx.button(
                    "Demo Chart",
                    on_click=State.show_demo_chart,
                    variant="surface",
                    color_scheme="blue",
                ),
                spacing="2",
                width="100%",
                align_items="center",
            ),
            rx.divider(),
            rx.heading("Performance Scorecard", size="5"),
            rx.grid(
                rx.card(
                    rx.vstack(
                        rx.text("Net PnL", size="1"),
                        rx.heading(
                            f"£{State.net_pnl}",
                            size="6",
                            color=rx.cond(State.net_pnl >= 0, "green", "red"),
                        ),
                    )
                ),
                rx.card(
                    rx.vstack(
                        rx.text("Win Rate", size="1"),
                        rx.heading(f"{State.win_rate}%", size="6"),
                    )
                ),
                rx.card(
                    rx.vstack(
                        rx.text("Profit Factor", size="1"),
                        rx.heading(State.profit_factor.to_string(), size="6"),
                    )
                ),
                rx.card(
                    rx.vstack(
                        rx.text("Total Trades", size="1"),
                        rx.heading(State.total_trades.to_string(), size="6"),
                    )
                ),
                rx.card(
                    rx.vstack(
                        rx.text("AI Conv. Rate", size="1"),
                        rx.heading(f"{State.conversion_rate}%", size="6"),
                    )
                ),
                columns="5",
                spacing="2",
                width="100%",
            ),
            rx.divider(),
            rx.heading("Cumulative PnL", size="5"),
            rx.recharts.area_chart(
                rx.recharts.area(
                    data_key="cumulative_pnl",
                    stroke="#3b82f6",
                    fill="#3b82f6",
                ),
                rx.recharts.x_axis(data_key="date"),
                rx.recharts.y_axis(),
                rx.recharts.cartesian_grid(stroke_dasharray="3 3"),
                rx.recharts.tooltip(),
                data=State.pnl_history,
                width="100%",
                height=300,
            ),
            rx.divider(),
            rx.heading("Recent Trades", size="5"),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Date"),
                        rx.table.column_header_cell("Ticker"),
                        rx.table.column_header_cell("Action"),
                        rx.table.column_header_cell("Entry"),
                        rx.table.column_header_cell("Init SL"),
                        rx.table.column_header_cell("Final SL"),
                        rx.table.column_header_cell("Exit"),
                        rx.table.column_header_cell("PnL"),
                        rx.table.column_header_cell("Outcome"),
                        rx.table.column_header_cell("View"),
                    ),
                ),
                rx.table.body(
                    rx.foreach(
                        State.trades,
                        lambda trade: rx.table.row(
                            rx.table.cell(trade["timestamp"]),
                            rx.table.cell(trade["epic"]),
                            rx.table.cell(trade["action"]),
                            rx.table.cell(trade["entry"]),
                            rx.table.cell(trade["initial_stop_loss"]),
                            rx.table.cell(trade["stop_loss"]),
                            rx.table.cell(trade["exit_price"]),
                            rx.table.cell(trade["pnl"]),
                            rx.table.cell(trade["outcome"]),
                            rx.table.cell(
                                rx.button(
                                    rx.icon("chart-line"),
                                    size="1",
                                    variant="ghost",
                                    on_click=lambda: State.open_trade_detail(trade),
                                )
                            ),
                        ),
                    )
                ),
                width="100%",
            ),
            spacing="4",  # Place spacing as the last keyword argument of rx.vstack
        ),
        width="100%",
        padding="2em",
    )


app = rx.App(theme=rx.theme(appearance="dark"))
app.add_page(index, on_load=State.load_data)
