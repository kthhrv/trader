import logging
from datetime import date
import holidays

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

    def is_holiday(self, epic: str, check_date: date = None) -> bool:
        """
        Determines if the market associated with the epic is closed due to a holiday.
        
        Args:
            epic (str): The instrument epic (e.g., "IX.D.FTSE.DAILY.IP").
            check_date (date, optional): The date to check. Defaults to today.
            
        Returns:
            bool: True if it is a holiday, False otherwise.
        """
        if check_date is None:
            check_date = date.today()
            
        # Determine country based on epic
        if "FTSE" in epic:
            is_hol = check_date in self.uk_holidays
            country = "UK"
        elif "SPX" in epic or "US500" in epic or "WALL" in epic or "NASDAQ" in epic or "US30" in epic or "SPTRD" in epic:
            is_hol = check_date in self.us_holidays
            country = "US"
        elif "NIKKEI" in epic or "JAPAN" in epic:
            is_hol = check_date in self.jp_holidays
            country = "Japan"
        elif "DAX" in epic or "DE30" in epic:
            # Add Germany if needed, for now default False or add holidays.Germany()
            # self.de_holidays = holidays.Germany()
            # is_hol = check_date in self.de_holidays
            is_hol = False # Default for now
            country = "Germany"
        else:
            # Default to False if unknown market, but log warning
            logger.warning(f"Unknown market for epic {epic}. Assuming open.")
            return False

        if is_hol:
            holiday_name = self._get_holiday_name(country, check_date)
            logger.info(f"Market {country} is CLOSED today ({check_date}) for {holiday_name}. Trading skipped.")
            return True
        else:
            logger.info(f"Market {country} is OPEN today ({check_date}).")
            
        return False

    def _get_holiday_name(self, country: str, check_date: date) -> str:
        if country == "UK":
            return self.uk_holidays.get(check_date)
        elif country == "US":
            return self.us_holidays.get(check_date)
        elif country == "Japan":
            return self.jp_holidays.get(check_date)
        return "Public Holiday"
