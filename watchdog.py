import time
import os
import logging
from datetime import datetime, timedelta
from src.notification_service import HomeAssistantNotifier

# Configure Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - watchdog - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

HEARTBEAT_FILE = "data/heartbeat.txt"
CHECK_INTERVAL = 60  # seconds
STALE_THRESHOLD_MINUTES = 5


def check_liveness():
    notifier = HomeAssistantNotifier()
    last_alert_sent = None

    logger.info(f"Watchdog started. Monitoring {HEARTBEAT_FILE}")

    while True:
        try:
            if not os.path.exists(HEARTBEAT_FILE):
                logger.warning(f"Heartbeat file {HEARTBEAT_FILE} missing!")
                is_stale = True
                time_diff_str = "File Missing"
            else:
                with open(HEARTBEAT_FILE, "r") as f:
                    content = f.read().strip()
                    last_heartbeat = datetime.fromisoformat(content)
                    time_diff = datetime.now() - last_heartbeat
                    is_stale = time_diff > timedelta(minutes=STALE_THRESHOLD_MINUTES)
                    time_diff_str = str(time_diff).split(".")[0]

            if is_stale:
                msg = f"Trader bot heartbeat is stale ({time_diff_str}). The process may be hung or crashed."
                logger.error(msg)

                # Rate limit alerts to once every 30 minutes
                if last_alert_sent is None or (
                    datetime.now() - last_alert_sent
                ) > timedelta(minutes=30):
                    notifier.send_notification(
                        title="CRITICAL: Trader Bot Liveness Alert",
                        message=msg,
                        priority="high",
                    )
                    last_alert_sent = datetime.now()
            else:
                logger.debug(f"Trader is alive. Last heartbeat: {time_diff_str} ago.")
                # Reset alert tracker if it becomes healthy again
                last_alert_sent = None

        except Exception as e:
            logger.error(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    check_liveness()
