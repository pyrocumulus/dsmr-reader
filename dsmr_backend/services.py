from distutils.version import StrictVersion
import re

import requests
from django.db.migrations.recorder import MigrationRecorder
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache

from dsmr_consumption.models.consumption import ElectricityConsumption, GasConsumption
from dsmr_weather.models.reading import TemperatureReading
from dsmr_weather.models.settings import WeatherSettings
from dsmr_datalogger.models.reading import DsmrReading
from dsmr_stats.models.statistics import DayStatistics
from dsmr_backup.models.settings import BackupSettings, DropboxSettings
from dsmr_mindergas.models.settings import MinderGasSettings
from dsmr_pvoutput.models.settings import PVOutputAddStatusSettings


def get_capabilities(capability=None):
    """
    Returns the capabilities of the data tracked, such as whether the meter supports gas readings or
    if there have been any readings regarding electricity being returned.

    Optionally returns a single capability when requested.
    """
    capabilities = cache.get('capabilities')  # Caching time should be very low, but enough to make it matter.

    if capabilities is None:
        capabilities = {
            # We rely on consumption because DSMR readings might be flushed in the future.
            'electricity': ElectricityConsumption.objects.exists(),
            'electricity_returned': ElectricityConsumption.objects.filter(
                # We can not rely on meter positions, as the manufacturer sometimes initializes meters
                # with testing data. So we just have to wait for the first power returned.
                currently_returned__gt=0
            ).exists(),
            'multi_phases': ElectricityConsumption.objects.filter(
                phase_currently_delivered_l2__isnull=False,
                phase_currently_delivered_l3__isnull=False
            ).exists(),
            'gas': GasConsumption.objects.exists(),
            'weather': WeatherSettings.get_solo().track and TemperatureReading.objects.exists()
        }

        # Override capabilities when requested.
        if settings.DSMRREADER_DISABLED_CAPABILITIES:
            for k in capabilities.keys():
                if k in settings.DSMRREADER_DISABLED_CAPABILITIES:
                    capabilities[k] = False

        capabilities['any'] = any(capabilities.values())
        cache.set('capabilities', capabilities)

    # Single selection.
    if capability is not None:
        return capabilities[capability]

    return capabilities


def is_latest_version():
    """ Checks whether the current version is the latest one available on Github. """
    response = requests.get(settings.DSMRREADER_LATEST_VERSION_FILE)

    local_version = '{}.{}.{}'.format(* settings.DSMRREADER_RAW_VERSION[:3])
    remote_version = re.search(r'^VERSION = \((\d+), (\d+), (\d+),', str(response.content, 'utf-8'), flags=re.MULTILINE)
    remote_version = '.'.join(remote_version.groups())

    return StrictVersion(local_version) >= StrictVersion(remote_version)


def is_timestamp_passed(timestamp):
    """ Generic service to check whether a timestamp has passed/is happening or is empty (None). """
    if timestamp is None:
        return True

    return timezone.now() >= timestamp


def get_electricity_status(capabilities):
    status = {
        'latest': None,
        'minutes_since': None,
    }

    if capabilities['electricity']:
        status['latest'] = ElectricityConsumption.objects.all().order_by('-pk')[0].read_at
        diff = timezone.now() - status['latest']
        status['minutes_since'] = round(diff.total_seconds() / 60)

    return status


def get_gas_status(capabilities):
    status = {
        'latest': None,
        'hours_since': None,
    }

    if capabilities['gas']:
        status['latest'] = GasConsumption.objects.all().order_by('-pk')[0].read_at
        diff = timezone.now() - status['latest']
        status['hours_since'] = round(diff.total_seconds() / 3600)

    return status


def get_reading_status():
    status = {
        'latest': None,
        'seconds_since': None,
        'unprocessed': {
            'count': None,
            'seconds_since': None,
        },
    }

    status['unprocessed']['count'] = DsmrReading.objects.unprocessed().count()

    try:
        first_unprocessed_reading = DsmrReading.objects.unprocessed().order_by('timestamp')[0]
    except IndexError:
        pass
    else:
        diff = timezone.now() - first_unprocessed_reading.timestamp
        status['unprocessed']['seconds_since'] = round(diff.total_seconds())

    try:
        status['latest'] = DsmrReading.objects.all().order_by('-pk')[0].timestamp
    except IndexError:
        pass
    else:
        # It seems that smart meters sometimes have their clock set into the future, so we correct it.
        if status['latest'] > timezone.now():
            status['seconds_since'] = 0
            status['latest'] = timezone.now()
        else:
            diff = timezone.now() - status['latest']
            status['seconds_since'] = round(diff.total_seconds())

    return status


def get_statistics_status():
    status = {
        'latest': None,
        'days_since': None,
    }

    try:
        status['latest'] = DayStatistics.objects.all().order_by('-day')[0].day
    except IndexError:
        pass
    else:
        status['days_since'] = (
            timezone.now().date() - status['latest']
        ).days

    return status


def status_info():
    """ Returns the status info of the application. """
    capabilities = get_capabilities()
    status = {
        'capabilities': capabilities,
        'electricity': get_electricity_status(capabilities),
        'gas': get_gas_status(capabilities),
        'readings': get_reading_status(),
        'statistics': get_statistics_status(),
        'tools':
        {
            'backup': {
                'enabled': False,
                'latest_backup': None,
            },
            'dropbox': {
                'enabled': False,
                'latest_sync': None,
            },
            'pvoutput': {
                'enabled': False,
                'latest_sync': None,
            },
            'mindergas': {
                'enabled': False,
                'latest_sync': None,
            },
        }
    }

    # (External) tools below.
    backup_settings = BackupSettings.get_solo()

    if backup_settings.daily_backup:
        status['tools']['backup']['enabled'] = True
        status['tools']['backup']['latest_backup'] = backup_settings.latest_backup

    dropbox_settings = DropboxSettings.get_solo()

    if dropbox_settings.access_token:
        status['tools']['dropbox']['enabled'] = True
        status['tools']['dropbox']['latest_sync'] = dropbox_settings.latest_sync

    pvoutput_settings = PVOutputAddStatusSettings.get_solo()

    if pvoutput_settings.export:
        status['tools']['pvoutput']['enabled'] = True
        status['tools']['pvoutput']['latest_sync'] = pvoutput_settings.latest_sync

    mindergas_settings = MinderGasSettings.get_solo()

    if mindergas_settings.export:
        status['tools']['mindergas']['enabled'] = True
        status['tools']['mindergas']['latest_sync'] = mindergas_settings.latest_sync

    return status


def is_recent_installation():
    """ Checks whether this is a new installation, by checking the interval to the first migration. """
    has_old_migration = MigrationRecorder.Migration.objects.filter(
        applied__lt=timezone.now() - timezone.timedelta(hours=1)
    ).exists()
    return not has_old_migration
