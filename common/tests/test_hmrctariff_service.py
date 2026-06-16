from django.test import TestCase, mock
from common.services.hmrctariff_service import HMRCTariffService

class TestHMRCTariffService(TestCase):
    """
    Unit tests for the HMRC Tariff Service.
    
    This class contains methods to test various functionalities of the HMRC API mock.
    """

    @mock.patch('common.services.hmrctariff_service.requests.get')
    def test_get_tariff_details_success(self, mock_get):
        """
        Test the retrieval of tariff details successfully from the HMRC API.
        
        This method mocks the requests.get call to simulate a successful API response.
        """
        # Setup mock response
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'commodity_code': '1234',
            'tariff_rate': 15.0,
            'description': 'Tariff details for commodity code 1234'
        }
        mock_get.return_value = mock_response

        # Instantiate the service and call the method
        tariff_service = HMRCTariffService()
        result = tariff_service.get_tariff_details('1234')

        # Assert the expected outcome
        self.assertEqual(result['commodity_code'], '1234')
        self.assertEqual(result['tariff_rate'], 15.0)
        mock_get.assert_called_once_with('https://api.hmrc.gov.uk/tariffs/details/1234', headers={'Authorization': 'Bearer test_token'})

    @mock.patch('common.services.hmrctariff_service.requests.get')
    def test_get_tariff_details_failure(self, mock_get):
        """
        Test the handling of a failed request to retrieve tariff details from the HMRC API.
        
        This method mocks the requests.get call to simulate a failure in the API response.
        """
        # Setup mock response
        mock_response = mock.Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        # Instantiate the service and call the method
        tariff_service = HMRCTariffService()
        with self.assertRaises(Exception):
            tariff_service.get_tariff_details('5678')

        # Assert that the expected exception is raised
        mock_get.assert_called_once_with('https://api.hmrc.gov.uk/tariffs/details/5678', headers={'Authorization': 'Bearer test_token'})

    @mock.patch('common.services.hmrctariff_service.requests.get')
    def test_get_tariff_details_exception(self, mock_get):
        """
        Test the handling of an exception when making a request to the HMRC API.
        
        This method mocks the requests.get call to simulate an exception in the API response.
        """
        # Setup mock response
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        # Instantiate the service and call the method
        tariff_service = HMRCTariffService()
        with self.assertRaises(Exception):
            tariff_service.get_tariff_details('9101')

        # Assert that the expected exception is raised
        mock_get.assert_called_once_with('https://api.hmrc.gov.uk/tariffs/details/9101', headers={'Authorization': 'Bearer test_token'})