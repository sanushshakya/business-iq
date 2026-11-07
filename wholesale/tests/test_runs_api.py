"""G44: delivery runs over the API — the manifest, proof-of-delivery photos, and who sees what. Fake data only."""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from authentication import refresh_tokens
from common.tests.factories import CompanyFactory, UserFactory
from inventory.models import Location
from tenants.roles import DRIVER, TILL
from wholesale.models import DeliveryRun, DeliveryStop, TradeCustomerAddress
from wholesale.tests.test_picking_api import pdf_text
from wholesale.tests.test_runs import Base

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64


def operator_client(user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_tokens.issue_token_pair(user)['access_token']}")
    return api


class RunApiTests(Base):
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=media)
        settings.enable()
        self.addCleanup(settings.disable)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)
        self.member = APIClient()
        self.member.force_authenticate(UserFactory(company=self.company))
        self.driver_api = operator_client(self.driver)
        TradeCustomerAddress.objects.create(customer=self.customer, label='Kitchen', line1='1 Fake Street',
                                            town='Testtown', postcode='TT1 1TT', is_default=True)

    def body(self, *orders, **fields):
        return {'run_date': str(self.today), 'vehicle': self.van.pk, 'driver': self.driver.pk,
                'orders': [order.pk for order in orders], **fields}

    def create(self, *orders, client=None, **fields):
        return (client or self.api).post(reverse('deliveryrun-list'), self.body(*orders, **fields), format='json')

    def post(self, run_id, name, *args, client=None, data=None, fmt='json'):
        return (client or self.api).post(reverse(f'deliveryrun-{name}', args=[run_id, *args]), data or {},
                                         format=fmt)

    def started(self, *orders):
        run_id = self.create(*orders).json()['id']
        self.assertEqual(self.post(run_id, 'start').status_code, 200)
        return run_id

    def test_create_start_deliver_complete(self):
        first, second = self.picked_order(), self.picked_order(rice=1)
        created = self.create(second, first, note='Fake note')
        self.assertEqual(created.status_code, 201, created.content)
        data = created.json()
        self.assertEqual((data['number_display'], data['status'], data['vehicle_code']), ('RUN-000001', 'planned',
                                                                                         'VAN-1'))
        self.assertEqual([stop['order'] for stop in data['stops']], [second.pk, first.pk])
        replanned = self.api.patch(reverse('deliveryrun-detail', args=[data['id']]),
                                   {'orders': [first.pk, second.pk]}, format='json')
        self.assertEqual([stop['order'] for stop in replanned.json()['stops']], [first.pk, second.pk])
        self.assertEqual(self.post(data['id'], 'start').json()['status'], 'out')
        stops = {stop['order']: stop['id'] for stop in self.api.get(
            reverse('deliveryrun-detail', args=[data['id']])).json()['stops']}
        photo = SimpleUploadedFile('door.png', PNG, content_type='image/png')
        delivered = self.post(data['id'], 'deliver', stops[first.pk], client=self.driver_api, fmt='multipart',
                              data={'received_by': 'Fake Chef', 'notes': 'Back door', 'photo': photo})
        self.assertEqual(delivered.status_code, 200, delivered.content)
        stop = next(s for s in delivered.json()['stops'] if s['id'] == stops[first.pk])
        self.assertEqual((stop['status'], stop['received_by'], stop['has_photo']), ('delivered', 'Fake Chef', True))
        self.assertNotIn('pod/', str(delivered.json()))  # the storage path is never shown
        failed = self.post(data['id'], 'fail', stops[second.pk], client=self.driver_api, data={'reason': 'Closed'})
        self.assertEqual(failed.status_code, 200, failed.content)
        self.assertEqual(self.post(data['id'], 'complete').json()['status'], 'completed')
        self.assertEqual(self.api.patch(reverse('deliveryrun-detail', args=[data['id']]), {'note': 'x'},
                                        format='json').status_code, 400)

    def test_photo_rules_and_access(self):
        run_id = self.started(self.picked_order())
        stop_id = DeliveryStop.objects.get().pk
        for name, content in (('door.gif', b'GIF89a' + b'\x00' * 20), ('door.png', b'not really a png'),
                              ('door.jpg', b'\xff\xd8\xff' + b'\x00' * (5 * 1024 * 1024))):
            with self.subTest(name=name):
                response = self.post(run_id, 'deliver', stop_id, fmt='multipart', data={
                    'received_by': 'Fake Chef', 'photo': SimpleUploadedFile(name, content)})
                self.assertEqual(response.status_code, 400)
        self.post(run_id, 'deliver', stop_id, fmt='multipart', data={
            'received_by': 'Fake Chef', 'photo': SimpleUploadedFile('door.jpg', JPEG)})
        url = reverse('deliveryrun-photo', args=[run_id, stop_id])
        for client, expected in ((self.api, 200), (self.driver_api, 200), (self.member, 403)):
            self.assertEqual(client.get(url).status_code, expected)
        self.assertEqual(b''.join(self.api.get(url).streaming_content), JPEG)
        other_driver, _ = get_user_model().objects.create_operator(self.company, 'Fake Driver Two', DRIVER)
        self.assertEqual(operator_client(other_driver).get(url).status_code, 404)

    def test_a_driver_sees_only_their_own_runs(self):
        mine = self.create(self.picked_order()).json()['id']
        other_driver, _ = get_user_model().objects.create_operator(self.company, 'Fake Driver Two', DRIVER)
        theirs = self.create(self.picked_order(), driver=other_driver.pk).json()['id']
        listed = [run['id'] for run in self.driver_api.get(reverse('deliveryrun-list')).json()['results']]
        self.assertEqual(listed, [mine])
        self.assertEqual(self.driver_api.get(reverse('deliveryrun-detail', args=[theirs])).status_code, 404)
        self.assertEqual(self.driver_api.get(reverse('deliveryrun-manifest', args=[mine])).status_code, 200)
        for name in ('start', 'complete', 'cancel'):
            self.assertEqual(self.post(mine, name, client=self.driver_api).status_code, 403)
        self.assertEqual(self.create(self.picked_order(), client=self.driver_api).status_code, 403)

    def test_roles(self):
        run_id = self.create(self.picked_order()).json()['id']
        self.assertEqual(self.member.get(reverse('deliveryrun-detail', args=[run_id])).status_code, 200)
        self.assertEqual(self.create(self.picked_order(), client=self.member).status_code, 403)
        self.assertEqual(self.post(run_id, 'start', client=self.member).status_code, 403)
        till, _ = get_user_model().objects.create_operator(self.company, 'Fake Till', TILL)
        self.assertEqual(operator_client(till).get(reverse('deliveryrun-list')).status_code, 403)

    def test_tenancy(self):
        order = self.picked_order()
        other = CompanyFactory()
        their_van = Location.objects.create(company=other, code='VAN-T', name='Theirs', type='van')
        their_driver, _ = get_user_model().objects.create_operator(other, 'Fake Other', DRIVER)
        for fields in ({'vehicle': their_van.pk}, {'driver': their_driver.pk}):
            with self.subTest(fields=list(fields)):
                self.assertEqual(self.create(order, **fields).status_code, 400)
        theirs = DeliveryRun.objects.create(company=other, number=1, run_date=self.today, vehicle=their_van,
                                            driver=their_driver)
        self.assertEqual(self.api.get(reverse('deliveryrun-detail', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.post(theirs.pk, 'start').status_code, 404)
        response = self.create(order, company=other.pk)
        self.assertEqual(DeliveryRun.objects.get(pk=response.json()['id']).company, self.company)

    def test_manifest(self):
        first, second = self.picked_order(), self.picked_order(rice=1)
        run_id = self.create(second, first).json()['id']
        response = self.api.get(reverse('deliveryrun-manifest', args=[run_id]))
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(response.content.startswith(b'%PDF'))
        text = pdf_text(response.content)
        for expected in ('RUN-000001', 'VAN-1', 'Fake Driver', '1 Fake Street', 'Fake Rice', 'Received by'):
            self.assertIn(expected, text)
        self.assertLess(text.index(second.number_display), text.index(first.number_display))

    def test_filters(self):
        planned = self.create(self.picked_order()).json()['id']
        out = self.started(self.picked_order())

        def ids(**params):
            response = self.api.get(reverse('deliveryrun-list'), params)
            self.assertEqual(response.status_code, 200, response.content)
            return sorted(run['id'] for run in response.json()['results'])

        self.assertEqual(ids(status='planned'), [planned])
        self.assertEqual(ids(status='out'), [out])
        self.assertEqual(ids(date=str(self.today)), sorted([planned, out]))
        self.assertEqual(self.api.get(reverse('deliveryrun-list'), {'status': 'x'}).status_code, 400)
