import logging
import random
import json

from django.utils import timezone
import requests

from dsmr_mindergas.models.settings import MinderGasSettings
from dsmr_consumption.models.consumption import GasConsumption
import dsmr_backend.services


logger = logging.getLogger('commands')


def should_export():
    """ Checks whether we should export data yet. Once every day. """
    mindergas_settings = MinderGasSettings.get_solo()

    # Only when enabled and token set.
    if not mindergas_settings.export or not mindergas_settings.auth_token:
        return False

    # Nonsense when having no data.
    if not dsmr_backend.services.get_capabilities(capability='gas'):
        return False

    return dsmr_backend.services.is_timestamp_passed(timestamp=mindergas_settings.next_export)


def export():
    """ Exports gas readings to the MinderGas website. """
    if not should_export():
        return

    logger.debug(' - MinderGas | Attempting to upload gas meter position.')

    # Just post the latest reading of the day before.
    today = timezone.localtime(timezone.now())
    midnight = timezone.make_aware(timezone.datetime(
        year=today.year,
        month=today.month,
        day=today.day,
        hour=0,
    ))

    # Push back for a day and a bit.
    next_export = midnight + timezone.timedelta(hours=24, minutes=random.randint(15, 59))
    mindergas_settings = MinderGasSettings.get_solo()

    try:
        last_gas_reading = GasConsumption.objects.filter(
            # Slack of six hours to make sure we have any valid reading at all.
            read_at__range=(midnight - timezone.timedelta(hours=6), midnight)
        ).order_by('-read_at')[0]
    except IndexError:
        # Just continue, even though we have no data... yet.
        last_gas_reading = None
        logger.error(' - MinderGas | No gas readings found for uploading')
    else:
        logger.debug(' - MinderGas | Uploading gas meter position: %s', last_gas_reading.delivered)

        # Register telegram by simply sending it to the application with a POST request.
        response = requests.post(
            MinderGasSettings.API_URL,
            headers={'Content-Type': 'application/json', 'AUTH-TOKEN': mindergas_settings.auth_token},
            data=json.dumps({
                'date': last_gas_reading.read_at.date().isoformat(),
                'reading': str(last_gas_reading.delivered)
            }),
        )

        if response.status_code != 201:
            # Try again in an hour.
            next_export = timezone.now() + timezone.timedelta(hours=1)
            logger.error(' [!] MinderGas upload failed (HTTP %s): %s', response.status_code, response.text)
        else:
            # Keep track.
            mindergas_settings.latest_sync = timezone.now()

    logger.debug(' - MinderGas | Delaying the next upload until:%s', next_export)
    mindergas_settings.next_export = next_export
    mindergas_settings.save()
