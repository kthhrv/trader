import logging
from datetime import date, timedelta
import holidays
from typing import Optional

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

    def _get_country_code(self, epic: str) -> Optional[str]:
        """
        Maps an IG epic to a country code for holiday lookup.
        """
        if "FTSE" in epic:
            return "UK"
        elif "SPX" in epic or "US500" in epic or "WALL" in epic or "NASDAQ" in epic or "US30" in epic or "SPTRD" in epic:
            return "US"
        elif "NIKKEI" in epic or "JAPAN" in epic:
            return "JP"
        elif "DAX" in epic or "DE30" in epic:
            return "DE" # Assuming Germany for DAX
        return None

    def is_holiday(self, epic: str) -> bool:
        """
        Determines if the market associated with the epic is closed due to a holiday.
        
        Args:
            epic (str): The instrument epic (e.g., "IX.D.FTSE.DAILY.IP").
            
        Returns:
            bool: True if it is a holiday, False otherwise.
        """
        current_date = date.today()
        country_code = self._get_country_code(epic)

        if not country_code:
            logger.warning(f"Unknown market for epic {epic}. Assuming open.")
            return False

        is_hol = False
        holiday_name = None

        if country_code == "UK":
            if current_date in self.uk_holidays:
                is_hol = True
                holiday_name = self.uk_holidays.get(current_date)
        elif country_code == "US":
            if current_date in self.us_holidays:
                is_hol = True
                holiday_name = self.us_holidays.get(current_date)
        elif country_code == "JP":
            if current_date in self.jp_holidays:
                is_hol = True
                holiday_name = self.jp_holidays.get(current_date)
        elif country_code == "DE":
            # For now, default German holidays to False unless a specific library is used
            is_hol = False
            holiday_name = "German Public Holiday (Not checked)"

        if is_hol:
            logger.info(f"Market {country_code} is CLOSED today ({current_date}) for {holiday_name if holiday_name else 'Public Holiday'}. Trading skipped.")
            return True
        else:
            # logger.info(f"Market {country_code} is OPEN today ({current_date}).") # Too verbose for regular logging
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