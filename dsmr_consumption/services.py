from datetime import time
from decimal import Decimal, ROUND_UP
import logging
import pytz

from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db.models import Avg, Min, Max, Count
from django.db.utils import IntegrityError
from django.utils import timezone, formats

from dsmr_consumption.models.consumption import ElectricityConsumption, GasConsumption
from dsmr_consumption.models.settings import ConsumptionSettings
from dsmr_consumption.models.energysupplier import EnergySupplierPrice
from dsmr_datalogger.models.reading import DsmrReading
from dsmr_weather.models.reading import TemperatureReading
from dsmr_stats.models.note import Note
from dsmr_datalogger.models.statistics import MeterStatistics


logger = logging.getLogger('commands')


def compact_all():
    """ Compacts all unprocessed readings, capped by a max to prevent hanging backend. """
    for current_reading in DsmrReading.objects.unprocessed()[0:1024]:
        compact(dsmr_reading=current_reading)


def compact(dsmr_reading):
    """
    Compacts/converts DSMR readings to consumption data. Optionally groups electricity by minute.
    """
    grouping_type = ConsumptionSettings.get_solo().compactor_grouping_type

    # Grouping by minute requires some distinction and history checking.
    reading_start = timezone.datetime.combine(
        dsmr_reading.timestamp.date(),
        time(hour=dsmr_reading.timestamp.hour, minute=dsmr_reading.timestamp.minute),
    ).replace(tzinfo=pytz.UTC)

    if grouping_type == ConsumptionSettings.COMPACTOR_GROUPING_BY_MINUTE:
        system_time_past_minute = timezone.now() >= reading_start + timezone.timedelta(minutes=1)
        reading_past_minute_exists = DsmrReading.objects.filter(
            timestamp__gte=reading_start + timezone.timedelta(minutes=1)
        ).exists()

        # Postpone until the minute has passed on the system time. And when there are (new) readings beyond this minute.
        if not system_time_past_minute or not reading_past_minute_exists:
            return

    # Create consumption records.
    _compact_electricity(dsmr_reading=dsmr_reading, grouping_type=grouping_type, reading_start=reading_start)
    _compact_gas(dsmr_reading=dsmr_reading, grouping_type=grouping_type, reading_start=reading_start)

    dsmr_reading.processed = True
    dsmr_reading.save(update_fields=['processed'])

    # For backend logging in Supervisor.
    logger.debug(' - Processed reading: %s', dsmr_reading)


def _compact_electricity(dsmr_reading, grouping_type, reading_start):
    """
    Compacts any DSMR readings to electricity consumption records, optionally grouped.
    """
    # Electricity should be unique, because it's the reading with the smallest interval anyway.
    if grouping_type == ConsumptionSettings.COMPACTOR_GROUPING_BY_READING:
        try:
            ElectricityConsumption.objects.get_or_create(
                read_at=dsmr_reading.timestamp,
                delivered_1=dsmr_reading.electricity_delivered_1,
                returned_1=dsmr_reading.electricity_returned_1,
                delivered_2=dsmr_reading.electricity_delivered_2,
                returned_2=dsmr_reading.electricity_returned_2,
                currently_delivered=dsmr_reading.electricity_currently_delivered,
                currently_returned=dsmr_reading.electricity_currently_returned,
                phase_currently_delivered_l1=dsmr_reading.phase_currently_delivered_l1,
                phase_currently_delivered_l2=dsmr_reading.phase_currently_delivered_l2,
                phase_currently_delivered_l3=dsmr_reading.phase_currently_delivered_l3,
                phase_currently_returned_l1=dsmr_reading.phase_currently_returned_l1,
                phase_currently_returned_l2=dsmr_reading.phase_currently_returned_l2,
                phase_currently_returned_l3=dsmr_reading.phase_currently_returned_l3,
            )
        except IntegrityError:
            # This might happen, even though rarely, when the same timestamp with different values comes by.
            pass

        return

    minute_end = reading_start + timezone.timedelta(minutes=1)

    # We might have multiple readings per minute, so there is a chance we already parsed it a moment ago.
    if ElectricityConsumption.objects.filter(read_at=minute_end).exists():
        return

    grouped_reading = DsmrReading.objects.filter(
        timestamp__gte=reading_start, timestamp__lt=minute_end
    ).aggregate(
        avg_delivered=Avg('electricity_currently_delivered'),
        avg_returned=Avg('electricity_currently_returned'),
        max_delivered_1=Max('electricity_delivered_1'),
        max_delivered_2=Max('electricity_delivered_2'),
        max_returned_1=Max('electricity_returned_1'),
        max_returned_2=Max('electricity_returned_2'),
        avg_phase_delivered_l1=Avg('phase_currently_delivered_l1'),
        avg_phase_delivered_l2=Avg('phase_currently_delivered_l2'),
        avg_phase_delivered_l3=Avg('phase_currently_delivered_l3'),
        avg_phase_return_l1=Avg('phase_currently_returned_l1'),
        avg_phase_return_l2=Avg('phase_currently_returned_l2'),
        avg_phase_return_l3=Avg('phase_currently_returned_l3'),
    )

    # This instance is the average/max and combined result.
    ElectricityConsumption.objects.create(
        read_at=minute_end,
        delivered_1=grouped_reading['max_delivered_1'],
        returned_1=grouped_reading['max_returned_1'],
        delivered_2=grouped_reading['max_delivered_2'],
        returned_2=grouped_reading['max_returned_2'],
        currently_delivered=grouped_reading['avg_delivered'],
        currently_returned=grouped_reading['avg_returned'],
        phase_currently_delivered_l1=grouped_reading['avg_phase_delivered_l1'],
        phase_currently_delivered_l2=grouped_reading['avg_phase_delivered_l2'],
        phase_currently_delivered_l3=grouped_reading['avg_phase_delivered_l3'],
        phase_currently_returned_l1=grouped_reading['avg_phase_return_l1'],
        phase_currently_returned_l2=grouped_reading['avg_phase_return_l2'],
        phase_currently_returned_l3=grouped_reading['avg_phase_return_l3'],
    )


def _compact_gas(dsmr_reading, grouping_type, **kwargs):
    """
    Compacts any DSMR readings to gas consumption records, optionally grouped. Only when there is support for gas.

    There is quite some distinction between DSMR v4 and v5. DSMR v4 will update only once per hour and backtracks the
    time by reporting it over the previous hour.
    DSMR v5 will just allow small intervals, depending on whether the readings are grouped per minute or not.
    """
    if not dsmr_reading.extra_device_timestamp or not dsmr_reading.extra_device_delivered:
        # Some households aren't connected to a gas meter at all.
        return

    read_at = dsmr_reading.extra_device_timestamp
    dsmr_version = MeterStatistics.get_solo().dsmr_version

    # User requests grouping? We will truncate the 'seconds' marker, which will only affect DSMR v5 readings.
    if grouping_type == ConsumptionSettings.COMPACTOR_GROUPING_BY_MINUTE:
        read_at = read_at.replace(second=0, microsecond=0)

    # DSMR v4 readings should reflect to the previous hour, to keep it compatible with the existing implementation.
    if dsmr_version is not None and dsmr_version.startswith('4'):
        read_at = read_at - timezone.timedelta(hours=1)

    # We will not override data, just ignore it then. DSMR v4 will hit this a lot, DSMR v5 not.
    if GasConsumption.objects.filter(read_at=read_at).exists():
        return

    # DSMR does not expose current gas rate, so we have to calculate it ourselves, relative to the previous gas
    # consumption, if any.
    try:
        previous = GasConsumption.objects.all().order_by('-read_at')[0]
    except IndexError:
        gas_diff = 0
    else:
        gas_diff = dsmr_reading.extra_device_delivered - previous.delivered

    GasConsumption.objects.create(
        read_at=read_at,
        delivered=dsmr_reading.extra_device_delivered,
        currently_delivered=gas_diff
    )


def consumption_by_range(start, end):
    """ Calculates the consumption of a range specified. """
    electricity_readings = ElectricityConsumption.objects.filter(
        read_at__gte=start, read_at__lt=end,
    ).order_by('read_at')

    gas_readings = GasConsumption.objects.filter(
        read_at__gte=start, read_at__lt=end,
    ).order_by('read_at')

    return electricity_readings, gas_readings


def day_consumption(day):
    """ Calculates the consumption of an entire day. """
    consumption = {
        'day': day
    }
    day_start = timezone.make_aware(timezone.datetime(year=day.year, month=day.month, day=day.day))
    day_end = day_start + timezone.timedelta(days=1)

    try:
        # This WILL fail when we either have no prices at all or conflicting ranges.
        daily_energy_price = EnergySupplierPrice.objects.by_date(target_date=day)
    except (EnergySupplierPrice.DoesNotExist, EnergySupplierPrice.MultipleObjectsReturned):
        # Default to zero prices.
        daily_energy_price = EnergySupplierPrice()

    electricity_readings, gas_readings = consumption_by_range(start=day_start, end=day_end)

    if not electricity_readings.exists():
        raise LookupError("No electricity readings found for: {}".format(day))

    electricity_reading_count = electricity_readings.count()

    first_reading = electricity_readings[0]
    last_reading = electricity_readings[electricity_reading_count - 1]

    consumption['latest_consumption'] = last_reading
    consumption['electricity1'] = last_reading.delivered_1 - first_reading.delivered_1
    consumption['electricity2'] = last_reading.delivered_2 - first_reading.delivered_2
    consumption['electricity1_start'] = first_reading.delivered_1
    consumption['electricity1_end'] = last_reading.delivered_1
    consumption['electricity2_start'] = first_reading.delivered_2
    consumption['electricity2_end'] = last_reading.delivered_2
    consumption['electricity1_returned'] = last_reading.returned_1 - first_reading.returned_1
    consumption['electricity2_returned'] = last_reading.returned_2 - first_reading.returned_2
    consumption['electricity1_returned_start'] = first_reading.returned_1
    consumption['electricity1_returned_end'] = last_reading.returned_1
    consumption['electricity2_returned_start'] = first_reading.returned_2
    consumption['electricity2_returned_end'] = last_reading.returned_2
    consumption['electricity_merged'] = consumption['electricity1'] + consumption['electricity2']
    consumption['electricity_returned_merged'] = \
        consumption['electricity1_returned'] + consumption['electricity2_returned']

    # Cost per tariff + direction.
    consumption['electricity1_cost'] = round_decimal(
        (consumption['electricity1'] * daily_energy_price.electricity_delivered_1_price) -
        (consumption['electricity1_returned'] * daily_energy_price.electricity_returned_1_price)
    )
    consumption['electricity2_cost'] = round_decimal(
        (consumption['electricity2'] * daily_energy_price.electricity_delivered_2_price) -
        (consumption['electricity2_returned'] * daily_energy_price.electricity_returned_2_price)
    )

    # Totals.
    consumption['electricity_cost_merged'] = consumption['electricity1_cost'] + consumption['electricity2_cost']
    consumption['total_cost'] = round_decimal(consumption['electricity_cost_merged'])

    # Gas readings are optional, as not all meters support this.
    if gas_readings.exists():
        gas_reading_count = gas_readings.count()
        first_reading = gas_readings[0]
        last_reading = gas_readings[gas_reading_count - 1]
        consumption['gas'] = last_reading.delivered - first_reading.delivered
        consumption['gas_start'] = first_reading.delivered
        consumption['gas_end'] = last_reading.delivered
        consumption['gas_cost'] = round_decimal(
            consumption['gas'] * daily_energy_price.gas_price
        )
        consumption['total_cost'] += consumption['gas_cost']

    # Current prices as well.
    consumption['energy_supplier_price_electricity_delivered_1'] = daily_energy_price.electricity_delivered_1_price
    consumption['energy_supplier_price_electricity_delivered_2'] = daily_energy_price.electricity_delivered_2_price
    consumption['energy_supplier_price_electricity_returned_1'] = daily_energy_price.electricity_returned_1_price
    consumption['energy_supplier_price_electricity_returned_2'] = daily_energy_price.electricity_returned_2_price
    consumption['energy_supplier_price_gas'] = daily_energy_price.gas_price

    # Any notes of that day.
    consumption['notes'] = Note.objects.filter(day=day).values_list('description', flat=True)

    # Temperature readings are not mandatory as well.
    temperature_readings = TemperatureReading.objects.filter(
        read_at__gte=day_start, read_at__lt=day_end,
    ).order_by('read_at')
    consumption['lowest_temperature'] = temperature_readings.aggregate(
        avg_temperature=Min('degrees_celcius'),
    )['avg_temperature'] or 0
    consumption['highest_temperature'] = temperature_readings.aggregate(
        avg_temperature=Max('degrees_celcius'),
    )['avg_temperature'] or 0
    consumption['average_temperature'] = temperature_readings.aggregate(
        avg_temperature=Avg('degrees_celcius'),
    )['avg_temperature'] or 0
    consumption['average_temperature'] = round_decimal(consumption['average_temperature'])

    return consumption


def live_electricity_consumption(use_naturaltime=True):
    """ Returns the current latest/live consumption. """
    data = {}

    try:
        latest_reading = DsmrReading.objects.all().order_by('-pk')[0]
    except IndexError:
        # Don't even bother when no data available.
        return data

    latest_timestamp = latest_reading.timestamp

    # In case the smart meter is running a clock in the future.
    if latest_timestamp > timezone.now():
        latest_timestamp = timezone.now()

    data['timestamp'] = latest_timestamp
    data['currently_delivered'] = int(latest_reading.electricity_currently_delivered * 1000)
    data['currently_returned'] = int(latest_reading.electricity_currently_returned * 1000)

    if use_naturaltime:
        data['timestamp'] = naturaltime(data['timestamp'])

    try:
        # This WILL fail when we either have no prices at all or conflicting ranges.
        prices = EnergySupplierPrice.objects.by_date(target_date=timezone.now().date())
    except (EnergySupplierPrice.DoesNotExist, EnergySupplierPrice.MultipleObjectsReturned):
        return data

    # We need to current tariff to get the right price.
    tariff = MeterStatistics.get_solo().electricity_tariff
    cost_per_hour = None

    tariff_map = {
        1: prices.electricity_delivered_1_price,
        2: prices.electricity_delivered_2_price,
    }

    try:
        cost_per_hour = latest_reading.electricity_currently_delivered * tariff_map[tariff]
    except KeyError:
        pass
    else:
        data['cost_per_hour'] = formats.number_format(
            round_decimal(cost_per_hour)
        )

    return data


def round_decimal(decimal_price):
    """ Round the price to two decimals. """
    if not isinstance(decimal_price, Decimal):
        decimal_price = Decimal(str(decimal_price))

    return decimal_price.quantize(Decimal('.01'), rounding=ROUND_UP)


def calculate_slumber_consumption_watt():
    """ Groups all electricity readings to find the most constant consumption. """
    most_common = ElectricityConsumption.objects.filter(
        currently_delivered__gt=0
    ).values('currently_delivered').annotate(
        currently_delivered_count=Count('currently_delivered')
    ).order_by('-currently_delivered_count')[:5]

    if not most_common:
        return

    # We calculate the average among the most common consumption read.
    count = 0
    usage = 0

    for item in most_common:
        count += item['currently_delivered_count']
        usage += item['currently_delivered_count'] * item['currently_delivered']

    return round(usage / count * 1000)


def calculate_min_max_consumption_watt():
    """ Returns the lowest and highest Wattage consumed for each phase. """
    FIELDS = {
        'total_min': ('currently_delivered', ''),
        'total_max': ('currently_delivered', '-'),
        'l1_max': ('phase_currently_delivered_l1', '-'),
        'l2_max': ('phase_currently_delivered_l2', '-'),
        'l3_max': ('phase_currently_delivered_l3', '-'),
    }
    data = {}

    for name, args in FIELDS.items():
        field, sorting = args

        try:
            read_at, value = ElectricityConsumption.objects.filter(**{
                '{}__gt'.format(field): 0,  # Skip (obvious) zero values.
                '{}__isnull'.format(field): False,  # And skip empty data.
            }).order_by(
                '{}{}'.format(sorting, field)
            ).values_list('read_at', field)[0]
        except IndexError:
            continue

        data.update({
            name: (
                formats.date_format(timezone.localtime(read_at), 'DSMR_GRAPH_LONG_DATE_FORMAT'),
                int(value * 1000)
            )
        })

    return data


def clear_consumption():
    """ Clears ALL consumption data ever generated. """
    ElectricityConsumption.objects.all().delete()
    GasConsumption.objects.all().delete()


def summarize_energy_contracts():
    """ Returns a summery of all energy contracts and some statistics along them. """
    import dsmr_stats.services  # Prevents circular import.

    data = []

    for current in EnergySupplierPrice.objects.all().order_by('-start'):
        summary = dsmr_stats.services.range_statistics(start=current.start, end=current.end or timezone.now().date())

        data.append({
            'description': current.description,
            'start': current.start,
            'end': current.end,
            'summary': summary,
            'prices': {
                'electricity_delivered_1_price': current.electricity_delivered_1_price,
                'electricity_delivered_2_price': current.electricity_delivered_2_price,
                'gas_price': current.gas_price,
                'electricity_returned_1_price': current.electricity_returned_1_price,
                'electricity_returned_2_price': current.electricity_returned_2_price,
            }
        })

    return data
