from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.reverse import reverse
from config.authentication.models import User, Company

class MultiTenantFilteringTest(TestCase):
    """
    Test suite for multi-tenant filtering to ensure users from different companies cannot retrieve records belonging to other companies.
    """

    def setUp(self):
        self.client = APIClient()
        
        # Create companies and users for testing
        self.company1 = Company.objects.create(name='Company 1')
        self.user1 = User.objects.create(username='user1', email='user1@example.com', password='password123')
        self.user1.owner.company = self.company1
        self.user1.save()
        
        self.company2 = Company.objects.create(name='Company 2')
        self.user2 = User.objects.create(username='user2', email='user2@example.com', password='password456')
        self.user2.owner.company = self.company2
        self.user2.save()

        # Create a sample record for each company
        self.sample_record1 = SampleRecord.objects.create(company=self.company1, name='Sample Record 1')
        self.sample_record2 = SampleRecord.objects.create(company=self.company2, name='Sample Record 2')

    def test_user_cannot_access_other_company_records(self):
        """
        Test that a user from one company cannot access records belonging to another company.
        """

        # Authenticate as user1
        self.client.force_authenticate(user=self.user1)

        # Attempt to retrieve sample records for company1
        response = self.client.get(reverse('sample-record-list'))

        # Verify that only records from company1 are returned
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Sample Record 1')

        # Authenticate as user2
        self.client.force_authenticate(user=self.user2)

        # Attempt to retrieve sample records for company2
        response = self.client.get(reverse('sample-record-list'))

        # Verify that only records from company2 are returned
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Sample Record 2')

    def test_superuser_can_access_all_records(self):
        """
        Test that a superuser can access records from all companies.
        """

        # Create a superuser
        self.superuser = User.objects.create_superuser(username='superuser', email='superuser@example.com', password='password789')
        
        # Authenticate as the superuser
        self.client.force_authenticate(user=self.superuser)

        # Attempt to retrieve sample records for both companies
        response = self.client.get(reverse('sample-record-list'))

        # Verify that records from both companies are returned
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertListEqual([record['name'] for record in response.data], ['Sample Record 1', 'Sample Record 2'])