import unittest
from unittest.mock import patch
from datetime import datetime, date
import pytz
from src.market_status import MarketStatus


class TestMarketStatus(unittest.TestCase):
    def setUp(self):
        # Patch the holiday calendar classes during setup
        self.patcher_uk = patch("src.market_status.holidays.UnitedKingdom")
        self.mock_uk_holidays_cls = self.patcher_uk.start()

        self.patcher_nyse = patch("src.market_status.holidays.NYSE")
        self.mock_nyse_holidays_cls = self.patcher_nyse.start()

        self.patcher_japan = patch("src.market_status.holidays.Japan")
        self.mock_japan_holidays_cls = self.patcher_japan.start()

        self.patcher_australia = patch("src.market_status.holidays.Australia")
        self.mock_au_holidays_cls = self.patcher_australia.start()

        self.patcher_germany = patch("src.market_status.holidays.Germany")
        self.mock_de_holidays_cls = self.patcher_germany.start()

        # Patch datetime.now()
        self.patcher_datetime = patch("src.market_status.datetime")
        self.mock_datetime = self.patcher_datetime.start()
        # Default mock value
        self.mock_datetime.now.return_value = datetime(2025, 1, 1, tzinfo=pytz.UTC)

        # Now instantiate MarketStatus (it will use the patched classes)
        self.market_status = MarketStatus()

    def tearDown(self):
        self.patcher_uk.stop()
        self.patcher_nyse.stop()
        self.patcher_japan.stop()
        self.patcher_australia.stop()
        self.patcher_germany.stop()
        self.patcher_datetime.stop()

    def test_is_holiday_uk(self):
        mock_now = datetime(2025, 12, 25, 10, 0, tzinfo=pytz.UTC)
        self.mock_datetime.now.return_value = mock_now
        self.mock_uk_holidays_cls.return_value.__contains__.return_value = True

        self.assertTrue(self.market_status.is_holiday("IX.D.FTSE.DAILY.IP"))
        self.mock_uk_holidays_cls.return_value.__contains__.assert_called_with(
            date(2025, 12, 25)
        )

    def test_is_holiday_us(self):
        mock_now = datetime(2025, 7, 4, 15, 0, tzinfo=pytz.UTC)
        self.mock_datetime.now.return_value = mock_now
        self.mock_nyse_holidays_cls.return_value.__contains__.return_value = True

        self.assertTrue(self.market_status.is_holiday("IX.D.SPTRD.DAILY.IP"))
        self.mock_nyse_holidays_cls.return_value.__contains__.assert_called_with(
            date(2025, 7, 4)
        )

    def test_is_holiday_japan(self):
        mock_now = datetime(2025, 1, 1, 10, 0, tzinfo=pytz.UTC)
        self.mock_datetime.now.return_value = mock_now
        self.mock_japan_holidays_cls.return_value.__contains__.return_value = True

        self.assertTrue(self.market_status.is_holiday("IX.D.NIKKEI.DAILY.IP"))
        self.mock_japan_holidays_cls.return_value.__contains__.assert_called_with(
            date(2025, 1, 1)
        )

    def test_is_holiday_australia(self):
        mock_now = datetime(2025, 1, 26, 10, 0, tzinfo=pytz.UTC)
        self.mock_datetime.now.return_value = mock_now
        self.mock_au_holidays_cls.return_value.__contains__.return_value = True

        self.assertTrue(self.market_status.is_holiday("IX.D.ASX.MONTH1.IP"))
        self.mock_au_holidays_cls.return_value.__contains__.assert_called_with(
            date(2025, 1, 26)
        )

    def test_is_holiday_germany(self):
        mock_now = datetime(2025, 10, 3, 10, 0, tzinfo=pytz.UTC)
        self.mock_datetime.now.return_value = mock_now
        self.mock_de_holidays_cls.return_value.__contains__.return_value = True

        self.assertTrue(self.market_status.is_holiday("IX.D.DAX.DAILY.IP"))
        self.mock_de_holidays_cls.return_value.__contains__.assert_called_with(
            date(2025, 10, 3)
        )

    def test_is_holiday_date_rollover_australia(self):
        # MONDAY 11:00 PM UK (23:00 UTC) -> TUESDAY morning in Sydney
        mock_now_utc = datetime(2025, 1, 20, 23, 0, tzinfo=pytz.UTC)
        self.mock_datetime.now.side_effect = lambda tz: mock_now_utc.astimezone(tz)

        self.mock_au_holidays_cls.return_value.__contains__.return_value = True
        self.assertTrue(self.market_status.is_holiday("IX.D.ASX.MONTH1.IP"))

        self.mock_au_holidays_cls.return_value.__contains__.assert_called_with(
            date(2025, 1, 21)
        )

    def test_is_holiday_unsupported_epic(self):
        self.assertFalse(self.market_status.is_holiday("UNSUPPORTED.EPIC"))


if __name__ == "__main__":
    unittest.main()
