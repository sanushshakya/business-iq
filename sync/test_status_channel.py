from unittest import mock

from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings

from authentication.jwt_handler import encode_token
from common.routing import websocket_urlpatterns
from common.tests.factories import CompanyFactory, ProductFactory, UserFactory
from config.asgi import application
from sync.models import OutboundEvent, StorefrontConnection
from sync.status import broadcast, status_for

BASE = 'https://m18.example.com/'
IN_MEMORY = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


def event(connection, status='pending', attempts=0, last_error='', event_type='stock.changed'):
    return OutboundEvent.objects.create(connection=connection, event_type=event_type, payload={}, status=status,
                                        attempts=attempts, last_error=last_error)


class StatusForTests(TestCase):
    def setUp(self):
        self.company = CompanyFactory()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret='s')

    def test_no_connection_is_off(self):
        self.assertEqual(status_for(CompanyFactory().pk)['state'], 'off')

    def test_states(self):
        self.assertEqual(status_for(self.company.pk)['state'], 'green')  # connected, nothing waiting
        delivered = event(self.connection, status='delivered')
        status = status_for(self.company.pk)
        self.assertEqual((status['state'], status['last_push_at']), ('green', delivered.created_at.isoformat()))
        event(self.connection)
        self.assertEqual((status_for(self.company.pk)['state'], status_for(self.company.pk)['backlog']), ('amber', 1))
        event(self.connection, attempts=2, last_error='HTTP 502: bad gateway')
        status = status_for(self.company.pk)
        self.assertEqual((status['state'], status['backlog'], status['last_error']), ('red', 2, 'HTTP 502: bad gateway'))

    def test_failed_events_are_red(self):
        event(self.connection, status='failed', attempts=20, last_error='ConnectionError')
        status = status_for(self.company.pk)
        self.assertEqual((status['state'], status['failed'], status['backlog']), ('red', 1, 0))

    def test_only_the_companys_own_events_count(self):
        other = StorefrontConnection.objects.create(company=CompanyFactory(), base_url=BASE, webhook_secret='s')
        event(other, status='failed', last_error='theirs')
        status = status_for(self.company.pk)
        self.assertEqual((status['state'], status['failed'], status['last_error']), ('green', 0, ''))


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class StatusChannelTests(TransactionTestCase):
    # Not TestCase: channels runs consumer code through database_sync_to_async, which closes "old" connections,
    # and on Postgres that is the test's own connection inside its transaction.
    def setUp(self):
        self.company = CompanyFactory()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret='s')
        self.user = UserFactory(company=self.company)

    async def connect(self, user=None, protocols=None):
        token = encode_token(user or self.user) if protocols is None else None
        communicator = WebsocketCommunicator(application, '/ws/sync/',
                                             subprotocols=protocols if protocols is not None else ['bearer', token])
        connected, subprotocol = await communicator.connect()
        return communicator, connected, subprotocol

    def test_route_is_registered(self):
        self.assertEqual(str(websocket_urlpatterns[0].pattern), 'ws/sync/')

    async def test_sends_the_companys_status_on_connect(self):
        communicator, connected, subprotocol = await self.connect()
        self.assertTrue(connected)
        self.assertEqual(subprotocol, 'bearer')
        message = await communicator.receive_json_from()
        self.assertEqual((message['type'], message['state']), ('sync.status', 'green'))
        await communicator.disconnect()

    async def test_refuses_without_a_valid_token(self):
        for protocols in ([], ['bearer'], ['bearer', 'not-a-token'], ['other', 'x']):
            with self.subTest(protocols):
                communicator, connected, _ = await self.connect(protocols=protocols)
                self.assertFalse(connected)
                await communicator.disconnect()

    async def test_refuses_operators_inactive_users_and_users_without_a_company(self):
        from asgiref.sync import sync_to_async

        operator = await sync_to_async(UserFactory)(company=self.company, role='till')
        inactive = await sync_to_async(UserFactory)(company=self.company, is_active=False)
        homeless = await sync_to_async(UserFactory)(company=None)
        for user in (operator, inactive, homeless):
            with self.subTest(user.pk):
                communicator, connected, _ = await self.connect(user=user)
                self.assertFalse(connected)
                await communicator.disconnect()

    async def test_one_company_never_receives_anothers_messages(self):
        from asgiref.sync import sync_to_async

        other_company = await sync_to_async(CompanyFactory)()
        await sync_to_async(StorefrontConnection.objects.create)(company=other_company, base_url=BASE, webhook_secret='s')
        outsider = await sync_to_async(UserFactory)(company=other_company)
        mine, _, _ = await self.connect()
        theirs, _, _ = await self.connect(user=outsider)
        await mine.receive_json_from()
        await theirs.receive_json_from()

        await sync_to_async(event)(self.connection, status='failed', last_error='HTTP 500')
        await sync_to_async(broadcast)(self.company.pk)
        message = await mine.receive_json_from()
        self.assertEqual((message['state'], message['last_error']), ('red', 'HTTP 500'))
        self.assertTrue(await theirs.receive_nothing(timeout=0.3))
        await mine.disconnect()
        await theirs.disconnect()


class BroadcastHookTests(TestCase):
    """The tasks that change what the badge shows tell the company's channel."""

    def setUp(self):
        self.company = CompanyFactory()
        self.connection = StorefrontConnection.objects.create(company=self.company, base_url=BASE, webhook_secret='s')
        self.enterContext(mock.patch('sync.outbox.deliver_outbound_event.apply_async'))
        self.broadcast = self.enterContext(mock.patch('sync.tasks.broadcast'))

    @mock.patch('sync.tasks.requests.post')
    def test_a_delivery_attempt_broadcasts(self, post):
        from sync.tasks import deliver_outbound_event

        post.return_value = mock.Mock(status_code=200, text='')
        with self.captureOnCommitCallbacks(execute=True):
            deliver_outbound_event(event(self.connection).pk)
        self.broadcast.assert_called_with(self.company.pk)

        self.broadcast.reset_mock()
        post.return_value = mock.Mock(status_code=502, text='down')
        with self.assertLogs('sync.tasks', 'WARNING'), self.captureOnCommitCallbacks(execute=True):
            deliver_outbound_event(event(self.connection).pk)
        self.broadcast.assert_called_with(self.company.pk)

    def test_a_push_that_sent_something_broadcasts(self):
        from sync.tasks import push_storefront_stock

        with self.captureOnCommitCallbacks(execute=True):
            push_storefront_stock()
        self.broadcast.assert_not_called()  # nothing pending
        ProductFactory(company=self.company, external_id='p1')
        with self.captureOnCommitCallbacks(execute=True):
            push_storefront_stock()
        self.broadcast.assert_called_with(self.company.pk)

    def test_a_broken_channel_layer_never_fails_the_task(self):
        with mock.patch('sync.status.get_channel_layer', side_effect=RuntimeError('redis down')), \
                self.assertLogs('sync.status', 'WARNING'):
            broadcast(self.company.pk)


class RedisChannelLayerSettingsTests(TestCase):
    def test_the_socket_timeout_outlasts_the_blocking_read(self):
        """redis-py 8 times out reads after 5 s by default, exactly channels-redis's blocking read: consumers died."""
        import json
        import os
        import subprocess
        import sys

        from channels_redis.core import RedisChannelLayer

        code = ('import json, django; from django.conf import settings; django.setup(); '
                'print(json.dumps(settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"]))')
        env = {**os.environ, 'USE_REDIS_CHANNELS': 'True', 'DJANGO_SETTINGS_MODULE': 'config.settings'}
        hosts = json.loads(subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True,
                                          check=True).stdout.strip().splitlines()[-1])
        self.assertTrue(all(host['socket_timeout'] > RedisChannelLayer.brpop_timeout for host in hosts), hosts)
