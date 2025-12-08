import reflex as rx
import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# Add parent directory to path to import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from src.database import fetch_recent_trades
from src.market_status import MarketStatus
from src.ig_client import IGClient
from src.yfinance_client import YFinanceClient

class State(rx.State):
    """The app state."""
    trades: list[dict] = []
    pnl_history: list[dict] = []
    uk_status: str = "Checking..."
    us_status: str = "Checking..."
    jp_status: str = "Checking..."
    last_updated: str = ""

    # Trade Detail & Graph State
    selected_trade: dict = {}
    candle_data: list[dict] = []
    show_detail: bool = False
    is_loading_graph: bool = False
    graph_cache: dict = {}

    def load_data(self):
        """Fetch data from the database and update market status."""
        try:
            raw_trades = fetch_recent_trades(limit=50)
            
            # Process trades for Table (Keep original order: newest first)
            processed_trades = []
            for t in raw_trades:
                processed_t = {k: (v if v is not None else "") for k, v in t.items()}
                processed_trades.append(processed_t)
            self.trades = processed_trades

            # Process trades for Graph (Sort Oldest -> Newest)
            # Filter out trades with no PnL (open trades or errors)
            valid_trades = [t for t in raw_trades if t.get("pnl") is not None and isinstance(t.get("pnl"), (int, float))]
            valid_trades.sort(key=lambda x: x["timestamp"]) # Sort chronological

            cumulative_pnl = 0.0
            history = []
            for t in valid_trades:
                cumulative_pnl += t["pnl"]
                history.append({
                    "date": t["timestamp"], 
                    "pnl": t["pnl"], 
                    "cumulative_pnl": round(cumulative_pnl, 2)
                })
            self.pnl_history = history

        except Exception as e:
            print(f"Error fetching trades: {e}")
            self.trades = []
            self.pnl_history = []

        # Check Market Status
        ms = MarketStatus()
        self.uk_status = ms.get_market_status("IX.D.FTSE.DAILY.IP")
        self.us_status = ms.get_market_status("IX.D.SPTRD.DAILY.IP")
        self.jp_status = ms.get_market_status("IX.D.NIKKEI.DAILY.IP")
        
        self.last_updated = datetime.now().strftime("%H:%M:%S")

    def open_trade_detail(self, trade: dict):
        """Sets the selected trade and fetches historical data for the graph."""
        self.selected_trade = trade
        self.show_detail = True
        self.is_loading_graph = True
        self.candle_data = [] # Reset old data
        
        # Async-like yield to show loading state if needed, but for now we block simply
        # In production, this should be a background task or cached
        try:
            # Parse timestamps
            # timestamp format: ISO (e.g. 2023-10-27T10:00:00.123456)
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
                    # Fallback if exit time is not standard ISO
                    exit_dt = datetime.now()
            else:
                exit_dt = datetime.now()

            # Add formatted times for graph vertical lines
            # Update selected_trade with graph-ready time strings
            trade_updates = self.selected_trade.copy()
            trade_updates["entry_time_graph"] = entry_dt.strftime("%H:%M")
            trade_updates["exit_time_graph"] = exit_dt.strftime("%H:%M")

            # Calculate Trailing Activation Price (1.5R)
            activation_price = 0
            try:
                entry_val = trade.get("entry")
                stop_loss_val = trade.get("stop_loss")
                
                entry_price = float(entry_val) if entry_val is not None else 0.0
                stop_loss = float(stop_loss_val) if stop_loss_val is not None else 0.0
                action = trade.get("action", "")
                
                if entry_price != 0 and stop_loss != 0:
                    risk = abs(entry_price - stop_loss)
                    if action == "BUY":
                        activation_price = entry_price + (1.5 * risk)
                    elif action == "SELL":
                        activation_price = entry_price - (1.5 * risk)
            except Exception as e:
                print(f"Error calculating activation price: {e}")
            
            trade_updates["trailing_activation_price"] = activation_price

            self.selected_trade = trade_updates

            # Check Cache
            deal_id = trade.get("deal_id")
            if deal_id and str(deal_id) in self.graph_cache:
                print(f"Loading graph for {deal_id} from cache.")
                self.candle_data = self.graph_cache[str(deal_id)]
                self.is_loading_graph = False
                return

            # Define Window: Entry - 30m to Exit + 30m
            start_dt = entry_dt - timedelta(minutes=30)
            end_dt = exit_dt + timedelta(minutes=30)

            # Determine resolution based on duration to save API allowance
            duration = end_dt - start_dt
            if duration > timedelta(hours=24):
                resolution = "1H"
            elif duration > timedelta(hours=4):
                resolution = "5Min"
            else:
                resolution = "1Min"

            # Format for IG API: YYYY-MM-DD HH:MM:SS
            fmt = "%Y-%m-%d %H:%M:%S"
            
            # Use YFinanceClient to fetch (bypassing IG quota)
            client = YFinanceClient() 
            
            epic = trade.get("epic")

            print(f"DEBUG: Fetching YF data for epic='{epic}', resolution='{resolution}', start_dt='{start_dt}', end_dt='{end_dt}")
            
            df = client.fetch_historical_data_by_range(
                epic, resolution, start_dt.strftime(fmt), end_dt.strftime(fmt)
            )

            # Fallback for missing high-res data (common with yfinance pre-market)
            if df.empty and resolution == "1Min":
                print(f"DEBUG: 1Min data empty for {epic}. Retrying with 5Min resolution...")
                resolution = "5Min"
                df = client.fetch_historical_data_by_range(
                    epic, resolution, start_dt.strftime(fmt), end_dt.strftime(fmt)
                )

            if df.empty:
                print(f"No graph data found for {epic}")
                self.candle_data = []
                self.is_loading_graph = False
                return

            # Process DF for Recharts
            # DF has 'open', 'high', 'low', 'close', 'volume'
            # Index is DateTime usually, but let's check reset_index
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index()
            
            # The date column might be named 'date' or 'DateTime' depending on trading_ig version
            # But IGClient._process_historical_df usually leaves index as is.
            # Let's inspect columns after reset_index
            date_col = df.columns[0] # Assume first column is date after reset

            # Calculate Offset to Align Futures Data with Spot Entry
            entry_price = float(trade.get("entry", 0))
            price_offset = 0
            if entry_price > 0 and not df.empty:
                # Find the candle closest to the entry time, or just use the first one if simple alignment
                # Ideally, we align the 'close' of the candle near entry_dt to the entry_price.
                # But for simplicity and to keep the trend relative, aligning the FIRST candle's close
                # to the entry price might distort the start if entry was later.
                # Better: Calculate mean difference? No.
                # Simple heuristic: Shift so the *average* price matches the entry? No.
                # Let's shift so the price at 'entry_time' matches 'entry_price'.
                
                # Find row closest to entry_dt
                # entry_dt is already parsed
                # df[date_col] are timestamps.
                
                # Normalize timezones to avoid TypeError
                if pd.api.types.is_datetime64_any_dtype(df[date_col]):
                    df[date_col] = df[date_col].dt.tz_localize(None)
                entry_dt = entry_dt.replace(tzinfo=None)

                closest_idx = (df[date_col] - entry_dt).abs().idxmin()
                reference_price = df.iloc[closest_idx]['close']
                price_offset = entry_price - reference_price
                print(f"DEBUG: Aligning graph. Entry: {entry_price}, Ref Candle: {reference_price}, Offset: {price_offset}")

            data = []
            for _, row in df.iterrows():
                # Convert date to string
                ts = row[date_col]
                if isinstance(ts, (pd.Timestamp, datetime)):
                    ts_str = ts.strftime("%H:%M")
                else:
                    ts_str = str(ts)

                # Apply offset to align visuals
                aligned_price = row["close"] + price_offset

                data.append({
                    "time": ts_str,
                    "price": aligned_price
                })
            
            self.candle_data = data
            
            # Update Cache
            if deal_id:
                self.graph_cache[str(deal_id)] = data

            # Calculate Y-Axis Domain to include all markers
            prices = [d["price"] for d in data]
            if prices:
                min_price = min(prices)
                max_price = max(prices)
                
                # Include markers in range
                markers = [
                    trade_updates.get("entry", 0),
                    trade_updates.get("stop_loss", 0),
                    trade_updates.get("take_profit", 0),
                    trade_updates.get("trailing_activation_price", 0)
                ]
                
                for m in markers:
                    if m and float(m) > 0:
                        min_price = min(min_price, float(m))
                        max_price = max(max_price, float(m))
                
                # Add padding (0.2%)
                padding = (max_price - min_price) * 0.05
                if padding == 0: padding = max_price * 0.01 # Fallback
                
                trade_updates["graph_y_min"] = min_price - padding
                trade_updates["graph_y_max"] = max_price + padding
                self.selected_trade = trade_updates

        except Exception as e:
            print(f"Error fetching graph data: {e}")
            self.candle_data = []
        
        self.is_loading_graph = False

    def close_detail(self):
        self.show_detail = False


def status_badge(label: str, status: str) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text(label, font_size="0.8em", font_weight="bold"),
            rx.text(status, 
                   color=rx.cond(status.to_string().contains("CLOSED"), "red", "green"), 
                   font_weight="bold"),
        ),
        padding="1em",
    )

def trade_detail_modal() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Trade Analysis"),
            rx.dialog.description("Review trade execution and market context."),
            
            rx.vstack(
                rx.hstack(
                    rx.badge(State.selected_trade["epic"], size="3"),
                    rx.badge(State.selected_trade["action"], 
                             color_scheme=rx.cond(State.selected_trade["action"] == "BUY", "green", "red"),
                             size="3"),
                    spacing="2"
                ),
                rx.text(f"Outcome: {State.selected_trade['outcome']}"),
                rx.text(f"PnL: {State.selected_trade['pnl']}"),
                rx.text(f"Entry: {State.selected_trade['entry']} | Exit: {State.selected_trade['exit_price']}"),
                rx.text(f"Reasoning: {State.selected_trade['reasoning']}", size="1", color="gray"),
                
                rx.divider(),
                
                rx.cond(
                    State.is_loading_graph,
                    rx.spinner(),
                    rx.recharts.line_chart(
                        rx.recharts.line(
                            data_key="price",
                            stroke="#8884d8",
                            dot=False,
                        ),
                        rx.recharts.reference_line(
                            y=State.selected_trade["entry"], 
                            stroke="green", 
                            label="Entry",
                            stroke_dasharray="3 3"
                        ),

                        rx.recharts.reference_line(
                            y=State.selected_trade["stop_loss"], 
                            stroke="orange", 
                            label="SL",
                            stroke_dasharray="3 3"
                        ),
                        rx.recharts.reference_line(
                            y=State.selected_trade["take_profit"], 
                            stroke="purple", 
                            label="TP",
                            stroke_dasharray="3 3"
                        ),
                        rx.cond(
                            State.selected_trade["trailing_activation_price"].to(float) > 0,
                            rx.recharts.reference_line(
                                y=State.selected_trade["trailing_activation_price"], 
                                stroke="blue", 
                                label="Trailing Start",
                                stroke_dasharray="3 3"
                            ),
                        ),

                        rx.recharts.reference_line(
                            x=State.selected_trade["entry_time_graph"], 
                            stroke="green", 
                            stroke_dasharray="3 3"
                        ),
                        rx.recharts.reference_line(
                            x=State.selected_trade["exit_time_graph"], 
                            stroke="red", 
                            stroke_dasharray="3 3"
                        ),
                        rx.recharts.x_axis(data_key="time", hide=True), # Hide X axis labels if too crowded
                        rx.recharts.y_axis(domain=[State.selected_trade["graph_y_min"], State.selected_trade["graph_y_max"]]), # Auto scale with markers
                        rx.recharts.tooltip(),
                        data=State.candle_data,
                        width="100%",
                        height=300,
                    )
                ),
                spacing="4",
            ),
            
            rx.flex(
                rx.dialog.close(
                    rx.button("Close", on_click=State.close_detail),
                ),
                justify="end",
                margin_top="1em"
            ),
        ),
        open=State.show_detail,
        on_open_change=State.close_detail,
    )

def index() -> rx.Component:
    return rx.container(
        trade_detail_modal(),
        rx.vstack(
            rx.heading("Gemini Trader Bot", size="8"),
            
            rx.hstack(
                status_badge("London (FTSE)", State.uk_status),
                status_badge("New York (S&P)", State.us_status),
                status_badge("Tokyo (Nikkei)", State.jp_status),
                rx.spacer(),
                rx.text(f"Last Updated: {State.last_updated}", color="gray"),
                rx.button("Refresh", on_click=State.load_data),
                width="100%",
                align_items="center"
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
                            rx.table.cell(trade["exit_price"]),
                            rx.table.cell(trade["pnl"]),
                            rx.table.cell(trade["outcome"]),
                            rx.table.cell(
                                rx.button(
                                    rx.icon("chart-line"),
                                    size="1",
                                    variant="ghost",
                                    on_click=lambda: State.open_trade_detail(trade)
                                )
                            ),
                        ),
                    )
                ),
                width="100%"
            ),

            spacing="4",
            padding="2em",
        ),
        max_width="1200px"
    )

app = rx.App(theme=rx.theme(appearance="dark"))
app.add_page(index, on_load=State.load_data)
