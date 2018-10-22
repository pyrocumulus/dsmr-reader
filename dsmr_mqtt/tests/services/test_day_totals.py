from unittest import mock
from decimal import Decimal
import json

from django.test import TestCase
from django.utils import timezone

from dsmr_mqtt.models.settings import day_totals
from dsmr_datalogger.models.reading import DsmrReading
from dsmr_consumption.models.consumption import ElectricityConsumption, GasConsumption
from dsmr_consumption.models.energysupplier import EnergySupplierPrice
import dsmr_mqtt.services.callbacks


class TestServices(TestCase):
    def _create_dsmrreading(self):
        return DsmrReading.objects.create(
            timestamp=timezone.now(),
            electricity_delivered_1=1,
            electricity_returned_1=2,
            electricity_delivered_2=3,
            electricity_returned_2=4,
            electricity_currently_delivered=5,
            electricity_currently_returned=6,
        )


class TestDaytotals(TestServices):
    def setUp(self):
        self.json_settings = day_totals.JSONDayTotalsMQTTSettings.get_solo()
        self.split_topic_settings = day_totals.SplitTopicDayTotalsMQTTSettings.get_solo()
        self.reading = self._create_dsmrreading()

        # Mapping.
        self.json_settings.formatting = '''
[mapping]
# DATA = TOPIC PATH
electricity1 = aaa
electricity2 = bbb
electricity1_returned = ccc
electricity2_returned = ddd
electricity_merged = eee
electricity_returned_merged = fff
electricity1_cost = ggg
electricity2_cost = hhh
electricity_cost_merged = iii
gas = jjj
gas_cost = kkk
total_cost = lll
energy_supplier_price_electricity_delivered_1 = mmm
energy_supplier_price_electricity_delivered_2 = nnn
energy_supplier_price_electricity_returned_1 = ooo
energy_supplier_price_electricity_returned_2 = ppp
energy_supplier_price_gas = qqq
'''
        self.json_settings.save()

        self.split_topic_settings.formatting = '''
[mapping]
# DATA = JSON FIELD
electricity1 = dsmr/aaa
electricity2 = dsmr/bbb
electricity1_returned = dsmr/ccc
electricity2_returned = dsmr/ddd
electricity_merged = dsmr/eee
electricity_returned_merged = dsmr/fff
electricity1_cost = dsmr/ggg
electricity2_cost = dsmr/hhh
electricity_cost_merged = dsmr/iii

# Gas (if any)
gas = dsmr/jjj
gas_cost = dsmr/kkk
total_cost = dsmr/lll

# Your energy supplier prices (if set)
energy_supplier_price_electricity_delivered_1 = dsmr/mmm
energy_supplier_price_electricity_delivered_2 = dsmr/nnn
energy_supplier_price_electricity_returned_1 = dsmr/ooo
energy_supplier_price_electricity_returned_2 = dsmr/ppp
energy_supplier_price_gas = dsmr/qqq
'''
        self.split_topic_settings.save()

    @mock.patch('dsmr_mqtt.models.queue.Message.objects.create')
    def test_disabled(self, create_message_mock):
        self.assertFalse(self.json_settings.enabled)
        self.assertFalse(self.split_topic_settings.enabled)
        self.assertFalse(create_message_mock.called)

        dsmr_mqtt.services.callbacks.publish_day_consumption()
        self.assertFalse(create_message_mock.called)

    @mock.patch('dsmr_mqtt.models.queue.Message.objects.create')
    def test_no_data(self, create_message_mock):
        self.json_settings.enabled = True
        self.json_settings.save()

        self.assertFalse(create_message_mock.called)
        dsmr_mqtt.services.callbacks.publish_day_consumption()
        self.assertFalse(create_message_mock.called)

    @mock.patch('dsmr_mqtt.models.queue.Message.objects.create')
    def test_json(self, create_message_mock):
        # Required for consumption to return any data.
        ElectricityConsumption.objects.bulk_create([
            ElectricityConsumption(
                read_at=self.reading.timestamp,
                delivered_1=0,
                delivered_2=0,
                returned_1=0,
                returned_2=0,
                currently_delivered=0,
                currently_returned=0,
            ),
            ElectricityConsumption(
                read_at=self.reading.timestamp + timezone.timedelta(seconds=1),
                delivered_1=12,
                delivered_2=14,
                returned_1=3,
                returned_2=5,
                currently_delivered=0,
                currently_returned=0,
            ),
        ])

        # Should be okay now.
        self.json_settings.enabled = True
        self.json_settings.save()
        dsmr_mqtt.services.callbacks.publish_day_consumption()
        self.assertTrue(create_message_mock.called)

        _, _, result = create_message_mock.mock_calls[0]
        result = json.loads(result['payload'])

        # Without gas or costs.
        self.assertEqual(result['aaa'], '12.000')
        self.assertEqual(result['bbb'], '14.000')
        self.assertEqual(result['ccc'], '3.000')
        self.assertEqual(result['ddd'], '5.000')
        self.assertEqual(result['eee'], '26.000')
        self.assertEqual(result['fff'], '8.000')
        self.assertEqual(result['ggg'], '0.00')
        self.assertEqual(result['hhh'], '0.00')
        self.assertEqual(result['iii'], '0.00')
        self.assertEqual(result['lll'], '0.00')

        # With gas.
        GasConsumption.objects.create(
            read_at=self.reading.timestamp,
            delivered=1,
            currently_delivered=0,
        )
        GasConsumption.objects.create(
            read_at=self.reading.timestamp + timezone.timedelta(seconds=1),
            delivered=5.5,
            currently_delivered=0,
        )

        create_message_mock.reset_mock()
        dsmr_mqtt.services.callbacks.publish_day_consumption()

        _, _, result = create_message_mock.mock_calls[0]
        result = json.loads(result['payload'])

        self.assertEqual(result['jjj'], '4.500')
        self.assertEqual(result['kkk'], '0.00')

        # With costs.
        EnergySupplierPrice.objects.create(
            start=self.reading.timestamp,
            end=self.reading.timestamp,
            description='Test',
            electricity_delivered_1_price=3,
            electricity_delivered_2_price=5,
            electricity_returned_1_price=1,
            electricity_returned_2_price=2,
            gas_price=8,
        )
        create_message_mock.reset_mock()
        dsmr_mqtt.services.callbacks.publish_day_consumption()

        _, _, result = create_message_mock.mock_calls[0]
        result = json.loads(result['payload'])

        self.assertEqual(result['ggg'], '33.00')
        self.assertEqual(result['hhh'], '60.00')
        self.assertEqual(result['iii'], '93.00')
        self.assertEqual(result['lll'], '129.00')

        self.assertEqual(result['mmm'], '3.00000')
        self.assertEqual(result['nnn'], '5.00000')
        self.assertEqual(result['ooo'], '1.00000')
        self.assertEqual(result['ppp'], '2.00000')
        self.assertEqual(result['qqq'], '8.00000')

    @mock.patch('dsmr_mqtt.models.queue.Message.objects.create')
    def test_split_topic(self, create_message_mock):
        # Required for consumption to return any data.
        ElectricityConsumption.objects.bulk_create([
            ElectricityConsumption(
                read_at=self.reading.timestamp,
                delivered_1=0,
                delivered_2=0,
                returned_1=0,
                returned_2=0,
                currently_delivered=0,
                currently_returned=0,
            ),
            ElectricityConsumption(
                read_at=self.reading.timestamp + timezone.timedelta(seconds=1),
                delivered_1=12,
                delivered_2=14,
                returned_1=3,
                returned_2=5,
                currently_delivered=0,
                currently_returned=0,
            ),
        ])
        GasConsumption.objects.create(
            read_at=self.reading.timestamp,
            delivered=1,
            currently_delivered=0,
        )
        GasConsumption.objects.create(
            read_at=self.reading.timestamp + timezone.timedelta(seconds=1),
            delivered=5.5,
            currently_delivered=0,
        )
        EnergySupplierPrice.objects.create(
            start=self.reading.timestamp,
            end=self.reading.timestamp,
            description='Test',
            electricity_delivered_1_price=3,
            electricity_delivered_2_price=5,
            electricity_returned_1_price=1,
            electricity_returned_2_price=2,
            gas_price=8,
        )

        # Should be okay now.
        self.split_topic_settings.enabled = True
        self.split_topic_settings.save()
        dsmr_mqtt.services.callbacks.publish_day_consumption()
        self.assertTrue(create_message_mock.called)

        called_kwargs = [x[1] for x in create_message_mock.call_args_list]

        # Without gas or costs.
        self.assertIn({'payload': Decimal('12.000'), 'topic': 'dsmr/aaa'}, called_kwargs)
        self.assertIn({'payload': Decimal('14.000'), 'topic': 'dsmr/bbb'}, called_kwargs)
        self.assertIn({'payload': Decimal('3.000'), 'topic': 'dsmr/ccc'}, called_kwargs)
        self.assertIn({'payload': Decimal('5.000'), 'topic': 'dsmr/ddd'}, called_kwargs)
        self.assertIn({'payload': Decimal('26.000'), 'topic': 'dsmr/eee'}, called_kwargs)
        self.assertIn({'payload': Decimal('8.000'), 'topic': 'dsmr/fff'}, called_kwargs)

        self.assertIn({'payload': Decimal('4.500'), 'topic': 'dsmr/jjj'}, called_kwargs)
        self.assertIn({'payload': Decimal('36.00'), 'topic': 'dsmr/kkk'}, called_kwargs)

        self.assertIn({'payload': Decimal('33.00'), 'topic': 'dsmr/ggg'}, called_kwargs)
        self.assertIn({'payload': Decimal('60.00'), 'topic': 'dsmr/hhh'}, called_kwargs)
        self.assertIn({'payload': Decimal('93.00'), 'topic': 'dsmr/iii'}, called_kwargs)
        self.assertIn({'payload': Decimal('129.00'), 'topic': 'dsmr/lll'}, called_kwargs)

        self.assertIn({'payload': Decimal('3.00000'), 'topic': 'dsmr/mmm'}, called_kwargs)
        self.assertIn({'payload': Decimal('5.00000'), 'topic': 'dsmr/nnn'}, called_kwargs)
        self.assertIn({'payload': Decimal('1.00000'), 'topic': 'dsmr/ooo'}, called_kwargs)
        self.assertIn({'payload': Decimal('2.00000'), 'topic': 'dsmr/ppp'}, called_kwargs)
        self.assertIn({'payload': Decimal('8.00000'), 'topic': 'dsmr/qqq'}, called_kwargs)
