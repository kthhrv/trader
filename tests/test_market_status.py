import unittest
from unittest.mock import patch, MagicMock
from datetime import date
from src.market_status import MarketStatus

class TestMarketStatus(unittest.TestCase):
    
    def setUp(self):
        # Patch the holiday calendar classes during setup
        self.patcher_uk = patch('src.market_status.holidays.UnitedKingdom')
        self.mock_uk_holidays_cls = self.patcher_uk.start()
        
        self.patcher_nyse = patch('src.market_status.holidays.NYSE')
        self.mock_nyse_holidays_cls = self.patcher_nyse.start()
        
        self.patcher_japan = patch('src.market_status.holidays.Japan')
        self.mock_japan_holidays_cls = self.patcher_japan.start()

        # Patch date.today()
        self.patcher_date = patch('src.market_status.date')
        self.mock_date = self.patcher_date.start()
        
        # Now instantiate MarketStatus (it will use the patched classes)
        self.market_status = MarketStatus()

    def tearDown(self):
        self.patcher_uk.stop()
        self.patcher_nyse.stop()
        self.patcher_japan.stop()
        self.patcher_date.stop()

    def test_is_holiday_true_uk(self):
        self.mock_date.today.return_value = date(2025, 12, 25)
        self.mock_uk_holidays_cls.return_value.__contains__.return_value = True
        
        self.assertTrue(self.market_status.is_holiday("IX.D.FTSE.DAILY.IP"))
        self.mock_uk_holidays_cls.return_value.__contains__.assert_called_once_with(date(2025, 12, 25))
        self.mock_uk_holidays_cls.assert_called_once()

    def test_is_holiday_false_uk(self):
        self.mock_date.today.return_value = date(2025, 12, 24)
        self.mock_uk_holidays_cls.return_value.__contains__.return_value = False

        self.assertFalse(self.market_status.is_holiday("IX.D.FTSE.DAILY.IP"))
        self.mock_uk_holidays_cls.return_value.__contains__.assert_called_once_with(date(2025, 12, 24))
        self.mock_uk_holidays_cls.assert_called_once()

    def test_is_holiday_unsupported_epic(self):
        self.mock_date.today.return_value = date(2025, 1, 1)
        self.assertFalse(self.market_status.is_holiday("UNSUPPORTED.EPIC"))
        # For unsupported epic, no holiday class methods should be called.
        # The `_get_country_code` returns None, so holiday.CountryHoliday is not even attempted to be instantiated.

    def test_is_holiday_us_epic(self):
        self.mock_date.today.return_value = date(2025, 11, 26)
        self.mock_nyse_holidays_cls.return_value.__contains__.return_value = False

        self.assertFalse(self.market_status.is_holiday("IX.D.SPTRD.DAILY.IP"))
        self.mock_nyse_holidays_cls.return_value.__contains__.assert_called_once_with(date(2025, 11, 26))
        self.mock_nyse_holidays_cls.assert_called_once()

    def test_is_holiday_japan_epic(self):
        self.mock_date.today.return_value = date(2025, 1, 1)
        self.mock_japan_holidays_cls.return_value.__contains__.return_value = True

        self.assertTrue(self.market_status.is_holiday("IX.D.NIKKEI.DAILY.IP"))
        self.mock_japan_holidays_cls.return_value.__contains__.assert_called_once_with(date(2025, 1, 1))
        self.mock_japan_holidays_cls.assert_called_once()

if __name__ == '__main__':
    unittest.main()
