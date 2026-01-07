import logging
import requests
from config import HA_API_URL, HA_ACCESS_TOKEN, HA_NOTIFY_ENTITY

logger = logging.getLogger(__name__)


class HomeAssistantNotifier:
    """
    Sends notifications via Home Assistant's REST API.
    """

    def __init__(self):
        self.api_url = HA_API_URL
        self.token = HA_ACCESS_TOKEN
        self.notify_entity = HA_NOTIFY_ENTITY

        if not self.token:
            logger.warning(
                "Home Assistant Access Token not set. Notifications will be skipped."
            )

    def send_notification(self, title: str, message: str, priority: str = "normal"):
        """
        Sends a notification to the configured Home Assistant entity.

        Args:
            title: Title of the notification.
            message: Body text.
            priority: 'high' or 'normal'. 'high' will attempt to bypass silent mode.
        """
        if not self.token:
            return

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        # The service endpoint is usually /api/services/<domain>/<service>
        # e.g., /api/services/notify/mobile_app_pixel_8
        domain = "notify"
        service = self.notify_entity.replace("notify.", "")

        url = f"{self.api_url.rstrip('/')}/api/services/{domain}/{service}"

        data = {
            "title": title,
            "message": message,
        }

        if priority == "high":
            # Android/iOS specific data for critical alerts
            data["data"] = {
                "ttl": 0,
                "priority": "high",
                "channel": "alarm",  # Android
                "push": {"sound": {"name": "default", "critical": 1, "volume": 1.0}},
            }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=5)
            response.raise_for_status()
            logger.debug(f"Notification sent to {self.notify_entity}: {title}")
        except Exception as e:
            # We use print here to avoid infinite recursion if logging itself is broken
            print(f"Failed to send Home Assistant notification: {e}")


class HANotificationHandler(logging.Handler):
    """
    Custom logging handler that sends ERROR and CRITICAL logs to Home Assistant.
    """

    def __init__(self, notifier: HomeAssistantNotifier):
        super().__init__()
        self.notifier = notifier
        self.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                msg = self.format(record)
                # Keep the alert short
                title = f"TRADER ALERT: {record.levelname}"
                # Truncate message if too long
                short_msg = (msg[:200] + "...") if len(msg) > 200 else msg

                self.notifier.send_notification(
                    title=title, message=short_msg, priority="high"
                )
            except Exception:
                self.handleError(record)
