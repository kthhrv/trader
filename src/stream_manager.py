import logging
import subprocess
import json
import threading
from typing import Callable, Dict, Optional
from src.ig_client import IGClient
from config import IS_LIVE

logger = logging.getLogger(__name__)


class StreamManager:
    def __init__(self, ig_client: IGClient):
        self.ig_client = ig_client
        self.process: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.callbacks: Dict[
            str, Callable[[dict], None]
        ] = {}  # For epic-specific callbacks
        self._trade_callback: Optional[Callable[[dict], None]] = (
            None  # For trade updates
        )
        self.is_connected = threading.Event()  # Event to signal connection status

        self.ls_endpoint = "https://demo-apd.marketdatasystems.com"
        if IS_LIVE:
            self.ls_endpoint = "https://apd.marketdatasystems.com"

    def _read_stdout(self, pipe):
        for line in iter(pipe.readline, ""):
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    data = json.loads(line_str)
                    message_type = data.get("type")

                    if message_type == "price_update":
                        epic = data.get("epic")
                        if epic and epic in self.callbacks:
                            self.callbacks[epic](
                                data
                            )  # Call registered epic-specific callback
                        else:
                            logger.debug(f"Received unhandled price update: {data}")
                    elif message_type == "trade_update":
                        if self._trade_callback:
                            self._trade_callback(
                                data
                            )  # Call registered trade update callback
                        else:
                            logger.debug(f"Received unhandled trade update: {data}")
                    else:
                        logger.debug(f"Received unknown stream data type: {data}")
                except json.JSONDecodeError:
                    logger.warning(
                        f"Failed to decode JSON from Node.js stream: {line_str}"
                    )
            elif "[NODE_STREAM_INFO]" in line_str:
                logger.info(
                    f"[Node.js Stream] {line_str.replace('[NODE_STREAM_INFO] ', '')}"
                )
                if "[LS Status]: CONNECTED" in line_str:
                    self.is_connected.set()  # Signal connection is established
            elif "[NODE_STREAM_ERROR]" in line_str:
                logger.error(
                    f"[Node.js Stream] {line_str.replace('[NODE_STREAM_ERROR] ', '')}"
                )
            else:
                logger.debug(f"[Node.js Stream Raw]: {line_str}")

    def connect(self):
        """
        Spawns the Node.js stream service as a subprocess.
        """
        if self.process and self.process.poll() is None:
            logger.info("Node.js stream service already running.")
            return

        try:
            # Ensure REST tokens are fresh
            if not self.ig_client.authenticated:
                self.ig_client.authenticate()

            # Extract Tokens directly from headers of the TRADING service
            headers = self.ig_client.service.session.headers
            cst = headers.get("CST")
            xst = headers.get("X-SECURITY-TOKEN")
            account_id = self.ig_client.service.account_id

            if not cst or not xst or not account_id:
                raise ValueError(
                    "Could not find CST/XST tokens or account ID in IGClient session."
                )

            script_path = "src/stream_service.js"

            # Pass credentials and epic as arguments to Node.js script
            cmd = [
                "node",
                script_path,
                cst,
                xst,
                account_id,
                "PLACEHOLDER_EPIC",
                self.ls_endpoint,
            ]  # Epic is a placeholder, will be updated via subscribe_to_epic

            logger.info(f"Spawning Node.js stream service: {' '.join(cmd)}")
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
            )  # Ensure line-buffering

            self.reader_thread = threading.Thread(
                target=self._read_stdout, args=(self.process.stdout,)
            )
            self.reader_thread.daemon = True
            self.reader_thread.start()

            # Wait for connection status from Node.js
            if not self.is_connected.wait(
                timeout=10
            ):  # Wait up to 10 seconds for connection
                logger.warning(
                    "Node.js stream service did not report CONNECTED status within timeout."
                )
                # Consider killing the process if not connected
                self.stop()
            else:
                logger.info("Node.js stream service connected successfully.")

        except Exception as e:
            logger.error(f"Failed to start Node.js stream service: {e}")
            self.stop()
            raise

    def subscribe_to_epic(self, epic: str, callback: Callable[[dict], None]):
        """
        Subscribes to an epic via the Node.js stream service.
        This will actually send a command to the Node.js process.
        For simplicity for now, we will restart the Node.js process with the new epic.
        A more robust solution would be IPC for dynamic subscriptions.
        """
        if not self.is_connected.is_set():
            logger.warning("Stream not connected. Attempting to connect...")
            self.connect()
            if not self.is_connected.is_set():
                logger.error("Failed to connect for subscription.")
                return

        self.callbacks[epic] = callback
        logger.info(f"Restarting Node.js service to subscribe to {epic}")
        # Kill current process and restart with new epic
        self.stop()
        # Need to ensure self.ig_client is authenticated again if it expires
        self.connect_and_subscribe(
            epic, callback
        )  # Recursive call, but should resolve quickly

    def connect_and_subscribe(self, epic: str, callback: Callable[[dict], None]):
        """
        Helper to connect and subscribe to a single epic.
        """
        self.callbacks[epic] = callback

        # Re-authenticate to get fresh tokens if needed
        if not self.ig_client.authenticated:
            self.ig_client.authenticate()

        headers = self.ig_client.service.session.headers
        cst = headers.get("CST")
        xst = headers.get("X-SECURITY-TOKEN")
        account_id = self.ig_client.service.account_id

        if not cst or not xst or not account_id:
            raise ValueError("Could not find CST/XST tokens or account ID.")

        script_path = "src/stream_service.js"
        cmd = ["node", script_path, cst, xst, account_id, epic, self.ls_endpoint]

        logger.info(f"Spawning Node.js stream service for {epic}: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, text=True
        )  # Ensure line-buffering

        self.reader_thread = threading.Thread(
            target=self._read_stdout, args=(self.process.stdout,)
        )
        self.reader_thread.daemon = True
        self.reader_thread.start()

        if not self.is_connected.wait(timeout=10):
            logger.warning(
                f"Node.js stream service for {epic} did not report CONNECTED status within timeout."
            )
            self.stop()
        else:
            logger.info(f"Node.js stream service for {epic} connected successfully.")

    def subscribe_trade_updates(self, callback: Callable[[dict], None]):
        """
        Registers a callback function to receive trade-related stream updates.
        """
        self._trade_callback = callback
        logger.info("Registered callback for trade updates.")

    def stop(self):
        """
        Stops the Node.js stream service subprocess.
        """
        if self.process and self.process.poll() is None:
            logger.info("Terminating Node.js stream service.")
            self.process.terminate()  # or .kill()
            self.process.wait(timeout=5)
            if self.process.poll() is None:
                logger.warning(
                    "Node.js stream service did not terminate gracefully. Killing."
                )
                self.process.kill()
            self.process = None
            self.is_connected.clear()  # Clear connection status
        else:
            logger.info("Node.js stream service not running or already stopped.")

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=2)
            if self.reader_thread.is_alive():
                logger.warning("Reader thread did not join gracefully.")
            self.reader_thread = None

        logger.info("StreamManager stopped.")
