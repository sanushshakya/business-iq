import unittest
from unittest.mock import patch
from common.hijri_calendar_service import HijriCalendarService
from datetime import datetime

class TestHijriCalendarService(unittest.TestCase):
    """
    Unit tests for the HijriCalendarService class.
    """

    @patch('common.hijri_calendar_service.get_hijri_date')
    def test_get_event_dates(self, mock_get_hijri_date):
        """
        Test the get_event_dates method with a mock API response.
        """
        # Arrange
        hijri_date = '1440-01-01'
        mock_get_hijri_date.return_value = hijri_date

        service = HijriCalendarService()

        # Act
        event_dates = service.get_event_dates()

        # Assert
        mock_get_hijri_date.assert_called_once()
        self.assertEqual(event_dates, [datetime.strptime(hijri_date, '%Y-%m-%d')])

    @patch('common.hijri_calendar_service.get_hijri_date')
    def test_get_event_dates_with_no_events(self, mock_get_hijri_date):
        """
        Test the get_event_dates method with no events returned from the API.
        """
        # Arrange
        hijri_date = '1440-01-01'
        mock_get_hijri_date.return_value = hijri_date

        service = HijriCalendarService()

        # Act
        event_dates = service.get_event_dates()

        # Assert
        mock_get_hijri_date.assert_called_once()
        self.assertEqual(event_dates, [])

    @patch('common.hijri_calendar_service.get_hijri_date')
    def test_get_event_dates_with_exception(self, mock_get_hijri_date):
        """
        Test the get_event_dates method with an exception from the API.
        """
        # Arrange
        mock_get_hijri_date.side_effect = Exception("API error")

        service = HijriCalendarService()

        # Act
        with self.assertRaises(Exception) as context:
            event_dates = service.get_event_dates()

        # Assert
        mock_get_hijri_date.assert_called_once()
        self.assertEqual(str(context.exception), "API error")

if __name__ == '__main__':
    unittest.main()