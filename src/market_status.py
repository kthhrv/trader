import logging
from datetime import date, timedelta, datetime
import holidays
from typing import Optional
import pytz

logger = logging.getLogger(__name__)


class MarketStatus:
    """
    Checks if the current day is a public holiday for a specific market.
    """

    def __init__(self):
        # Initialize holiday calendars for key markets
        self.uk_holidays = holidays.UnitedKingdom()
        # Use NYSE calendar directly if available, otherwise default to US
        try:
            self.us_holidays = holidays.NYSE()
        except AttributeError:
            self.us_holidays = holidays.UnitedStates()

        self.jp_holidays = holidays.Japan()
        self.au_holidays = holidays.Australia()
        self.de_holidays = holidays.Germany()

    def _get_country_code(self, epic: str) -> Optional[str]:
        """
        Maps an IG epic to a country code for holiday lookup.
        """
        if "FTSE" in epic:
            return "UK"
        elif (
            "SPX" in epic
            or "US500" in epic
            or "WALL" in epic
            or "NASDAQ" in epic
            or "US30" in epic
            or "SPTRD" in epic
        ):
            return "US"
        elif "NIKKEI" in epic or "JAPAN" in epic:
            return "JP"
        elif "DAX" in epic or "DE30" in epic:
            return "DE"  # Assuming Germany for DAX
        elif "ASX" in epic or "AUS200" in epic:
            return "AU"
        return None

    def is_holiday(self, epic: str) -> bool:
        """
        Determines if the market associated with the epic is closed due to a holiday.
        Checks the holiday status for the date in the MARKET'S timezone, not local time.

        Args:
            epic (str): The instrument epic (e.g., "IX.D.FTSE.DAILY.IP").

        Returns:
            bool: True if it is a holiday, False otherwise.
        """
        country_code = self._get_country_code(epic)

        if not country_code:
            logger.warning(f"Unknown market for epic {epic}. Assuming open.")
            return False

        # Determine the date in the target market's timezone
        # This handles the case where it's Monday night in UK but Tuesday morning in AU/JP
        schedule = self._get_market_hours(epic)
        tz_name = schedule.get("timezone", "UTC")
        try:
            tz = pytz.timezone(tz_name)
            target_date = datetime.now(tz).date()
        except Exception as e:
            logger.warning(
                f"Timezone conversion failed for {epic} ({tz_name}): {e}. Using local date."
            )
            target_date = date.today()

        is_hol = False
        holiday_name = None

        if country_code == "UK":
            if target_date in self.uk_holidays:
                is_hol = True
                holiday_name = self.uk_holidays.get(target_date)
        elif country_code == "US":
            if target_date in self.us_holidays:
                is_hol = True
                holiday_name = self.us_holidays.get(target_date)
        elif country_code == "JP":
            if target_date in self.jp_holidays:
                is_hol = True
                holiday_name = self.jp_holidays.get(target_date)
        elif country_code == "DE":
            if target_date in self.de_holidays:
                is_hol = True
                holiday_name = self.de_holidays.get(target_date)
        elif country_code == "AU":
            if target_date in self.au_holidays:
                is_hol = True
                holiday_name = self.au_holidays.get(target_date)

        if is_hol:
            logger.info(
                f"Market {country_code} is CLOSED on {target_date} for {holiday_name if holiday_name else 'Public Holiday'}. Trading skipped."
            )
            return True
        else:
            # logger.info(f"Market {country_code} is OPEN on {target_date}.")
            return False

    def get_market_status(self, epic: str) -> str:
        """
        Returns a string indicating the market status (OPEN or CLOSED).
        This version relies only on holiday checks, not live market hours.
        """
        if self.is_holiday(epic):
            return "CLOSED (Holiday)"
        # In a real scenario, this would check specific market hours based on epic
        return "OPEN"

    def _get_market_hours(self, epic: str) -> dict:
        """
        Returns the market hours (open/close) for a given epic.
        Times are in the market's local timezone.
        """
        country = self._get_country_code(epic)

        # Default fallback
        schedule = {"open": "09:00", "close": "17:00", "timezone": "UTC"}

        if country == "UK":  # FTSE
            schedule = {"open": "08:00", "close": "16:30", "timezone": "Europe/London"}
        elif country == "US":  # SPX, NASDAQ, DOW
            schedule = {
                "open": "09:30",
                "close": "16:00",
                "timezone": "America/New_York",
            }
        elif country == "JP":  # Nikkei
            schedule = {"open": "09:00", "close": "15:00", "timezone": "Asia/Tokyo"}
        elif country == "DE":  # DAX
            schedule = {"open": "09:00", "close": "17:30", "timezone": "Europe/Berlin"}
        elif country == "AU":  # ASX
            schedule = {
                "open": "10:00",
                "close": "16:00",
                "timezone": "Australia/Sydney",
            }

        return schedule

    def get_market_close_time_str(self, epic: str) -> str:
        """
        Returns the market close time as a string (e.g., "16:30 Europe/London").
        """
        schedule = self._get_market_hours(epic)
        return f"{schedule['close']} ({schedule['timezone']})"

    def get_market_close_datetime(self, epic: str) -> datetime:
        """
        Returns the next market close time as a localized datetime object.
        """
        schedule = self._get_market_hours(epic)
        close_time_str = schedule["close"]
        timezone_str = schedule["timezone"]

        tz = pytz.timezone(timezone_str)
        now_tz = datetime.now(tz)

        hour, minute = map(int, close_time_str.split(":"))

        close_dt = now_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If it's already past today's close, the next close is tomorrow (simplification)
        if now_tz > close_dt:
            close_dt += timedelta(days=1)

        return close_dt
