from unittest import mock
import ssl

from django.test import TestCase
from django.conf import settings
import paho.mqtt.client as paho

from dsmr_mqtt.models.settings.broker import MQTTBrokerSettings
from dsmr_backend.mixins import StopInfiniteRun
from dsmr_mqtt.models import queue
import dsmr_mqtt.services.broker


class TestBroker(TestCase):
    @mock.patch('time.sleep')
    @mock.patch('paho.mqtt.client.Client.connect')
    def test_initialize_disabled(self, connect_mock, sleep_mock):
        """ Default, disabled settings. """
        self.assertFalse(connect_mock.called)
        self.assertFalse(sleep_mock.called)

        with self.assertRaises(StopInfiniteRun):
            dsmr_mqtt.services.broker.initialize()

        self.assertFalse(connect_mock.called)
        self.assertTrue(sleep_mock.called)

    @mock.patch('paho.mqtt.client.Client.connect')
    def test_initialize_enabled(self, connect_mock):
        """ MQTT support enabled. """
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost')

        self.assertFalse(connect_mock.called)

        mqtt_client = dsmr_mqtt.services.broker.initialize()

        self.assertIsNotNone(mqtt_client)
        self.assertTrue(connect_mock.called)

    @mock.patch('paho.mqtt.client.Client.connect')
    @mock.patch('paho.mqtt.client.Client.tls_set')
    def test_initialize_insecure(self, tls_set_mock, *mocks):
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost', secure=MQTTBrokerSettings.INSECURE)

        self.assertFalse(tls_set_mock.called)
        dsmr_mqtt.services.broker.initialize()
        self.assertFalse(tls_set_mock.called)

    @mock.patch('paho.mqtt.client.Client.connect')
    @mock.patch('paho.mqtt.client.Client.tls_set')
    def test_initialize_secure_cert_none(self, tls_set_mock, *mocks):
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost', secure=MQTTBrokerSettings.SECURE_CERT_NONE)

        self.assertFalse(tls_set_mock.called)
        dsmr_mqtt.services.broker.initialize()
        tls_set_mock.assert_called_with(cert_reqs=ssl.CERT_NONE)

    @mock.patch('paho.mqtt.client.Client.connect')
    @mock.patch('paho.mqtt.client.Client.tls_set')
    def test_initialize_secure_cert_required(self, tls_set_mock, *mocks):
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost', secure=MQTTBrokerSettings.SECURE_CERT_REQUIRED)

        self.assertFalse(tls_set_mock.called)
        dsmr_mqtt.services.broker.initialize()
        tls_set_mock.assert_called_with(cert_reqs=ssl.CERT_REQUIRED)

    @mock.patch('time.sleep')
    @mock.patch('paho.mqtt.client.Client.connect')
    def test_initialize_connection_refused(self, connect_mock, sleep_mock):
        """ Whenever the broker is unavailable. """
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost')

        connect_mock.side_effect = ConnectionRefusedError()  # Fail.
        self.assertFalse(sleep_mock.called)

        self.assertFalse(connect_mock.called)

        with self.assertRaises(StopInfiniteRun):
            dsmr_mqtt.services.broker.initialize()

        self.assertTrue(connect_mock.called)
        self.assertTrue(sleep_mock.called)

    @mock.patch('paho.mqtt.client.Client.connect')
    def test_initialize_debug(self, *mocks):
        """ Debug enabled. """
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost')

        mqtt_client = dsmr_mqtt.services.broker.initialize()

        self.assertIsNone(mqtt_client.on_log)
        self.assertIsNone(mqtt_client.on_publish)

        MQTTBrokerSettings.objects.update(debug=True)
        mqtt_client = dsmr_mqtt.services.broker.initialize()

        # Check callbacks set.
        self.assertEqual(mqtt_client.on_log, dsmr_mqtt.services.broker.on_log)
        self.assertEqual(mqtt_client.on_publish, dsmr_mqtt.services.broker.on_publish)

    @mock.patch('paho.mqtt.client.Client.connect')
    def test_initialize_credentials(self, *mocks):
        """ User/password set. """
        USER = 'x'
        PASS = 'y'
        MQTTBrokerSettings.get_solo()
        MQTTBrokerSettings.objects.update(hostname='localhost')

        mqtt_client = dsmr_mqtt.services.broker.initialize()

        self.assertIsNone(mqtt_client._username)
        self.assertIsNone(mqtt_client._password)

        MQTTBrokerSettings.objects.update(username=USER, password=PASS)
        mqtt_client = dsmr_mqtt.services.broker.initialize()

        # Check credentials set.
        self.assertEqual(mqtt_client._username, USER.encode('utf-8'))
        self.assertEqual(mqtt_client._password, PASS.encode('utf-8'))

    def test_on_connect(self):
        """ Coverage test. """
        client = mock.MagicMock()

        for x in range(0, 6):
            dsmr_mqtt.services.broker.on_connect(client, None, None, rc=x)

        dsmr_mqtt.services.broker.on_connect(client, None, None, rc=-1)

    @mock.patch('paho.mqtt.client.Client.loop')
    @mock.patch('paho.mqtt.client.Client.publish')
    def test_run(self, publish_mock, loop_mock):
        client = paho.Client('xxx')

        # Empty.
        self.assertFalse(loop_mock.called)
        self.assertFalse(publish_mock.called)

        dsmr_mqtt.services.broker.run(mqtt_client=client)

        self.assertTrue(loop_mock.called)
        self.assertFalse(publish_mock.called)

        # With queue.
        loop_mock.reset_mock()
        queue.Message.objects.create(topic='x', payload='y')
        self.assertFalse(loop_mock.called)
        self.assertFalse(publish_mock.called)

        dsmr_mqtt.services.broker.run(mqtt_client=client)

        self.assertTrue(loop_mock.called)
        self.assertTrue(publish_mock.called)

    @mock.patch('paho.mqtt.client.Client.loop')
    @mock.patch('paho.mqtt.client.Client.publish')
    def test_run_cleanup(self, publish_mock, *mocks):
        """ Test whether any excess of messages is cleared. """
        client = paho.Client('xxx')
        MAX = settings.DSMRREADER_MQTT_MAX_MESSAGES_IN_QUEUE

        for x in range(1, MAX * 2 + 1):
            queue.Message.objects.create(topic='z', payload=x)

        self.assertEqual(queue.Message.objects.count(), MAX * 2)

        dsmr_mqtt.services.broker.run(mqtt_client=client)

        self.assertEqual(publish_mock.call_count, MAX)

        # We assert that the LAST X messages were sent, rest is deleted.
        for x in range(MAX + 1, MAX * 2 + 1):
            publish_mock.assert_any_call(topic='z', payload=str(x), qos=0)

        self.assertFalse(queue.Message.objects.exists())

    @mock.patch('paho.mqtt.client.Client.reconnect')
    def test_on_disconnect(self, reconnect_mock):
        """ Test callback reconnect, when required. """
        client = paho.Client('xxx')

        # Normal exit.
        self.assertFalse(reconnect_mock.called)
        dsmr_mqtt.services.broker.on_disconnect(client, None, rc=0)
        self.assertFalse(reconnect_mock.called)

    @mock.patch('paho.mqtt.client.Client.reconnect')
    def test_reconnect_okay(self, reconnect_mock):
        """ Test callback reconnect, when required. """
        client = paho.Client('xxx')

        # Unexpected exit.
        self.assertFalse(reconnect_mock.called)
        dsmr_mqtt.services.broker.on_disconnect(client, None, rc=1)
        self.assertTrue(reconnect_mock.called)

    @mock.patch('paho.mqtt.client.Client.reconnect')
    def test_reconnect_failed(self, reconnect_mock):
        """ Test callback reconnect, but still failing. """
        reconnect_mock.side_effect = ConnectionRefusedError()  # Fail.
        client = paho.Client('xxx')

        # Still failing, command should be stopped.
        with self.assertRaises(StopInfiniteRun):
            dsmr_mqtt.services.broker.on_disconnect(client, None, rc=1)

    def test_on_log(self):
        """ Coverage test. """
        dsmr_mqtt.services.broker.on_log(None, userdata='x', level='y', buf='z')

    def test_on_publish(self):
        """ Coverage test. """
        dsmr_mqtt.services.broker.on_publish(None, userdata='x', mid='y')
