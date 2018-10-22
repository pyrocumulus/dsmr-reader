from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext as _
from django.contrib.auth.models import User
from django.conf import settings

from dsmr_backup.models.settings import BackupSettings, DropboxSettings
from dsmr_notification.models.settings import NotificationSetting
from dsmr_consumption.models.settings import ConsumptionSettings
from dsmr_mqtt.models.settings.broker import MQTTBrokerSettings
from dsmr_pvoutput.models.settings import PVOutputAPISettings
from dsmr_mindergas.models.settings import MinderGasSettings
from dsmr_frontend.models.message import Notification
from dsmr_api.models import APISettings
from dsmr_mqtt.models import queue


class Command(BaseCommand):
    help = _('Resets the environment for development purposes. Not intended for production.')

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)
        parser.add_argument(
            '--no-api',
            action='store_true',
            dest='no_api',
            default=False,
            help=_('Whether the API should be disabled.')
        )

    def handle(self, **options):
        if not settings.DEBUG:
            raise CommandError(_('Intended usage is NOT production! Only allowed when DEBUG = True'))

        # Just wipe all settings which can affect the environment.
        APISettings.objects.update(allow=not options['no_api'], auth_key='test')
        BackupSettings.objects.update(daily_backup=False)
        DropboxSettings.objects.update(access_token=None)
        ConsumptionSettings.objects.update(compactor_grouping_type=ConsumptionSettings.COMPACTOR_GROUPING_BY_READING)
        MinderGasSettings.objects.update(export=False, auth_token=None)
        NotificationSetting.objects.update(
            notification_service=None, pushover_api_key=None, pushover_user_key=None, prowl_api_key=None
        )
        MQTTBrokerSettings.objects.update(
            port=8883, secure=MQTTBrokerSettings.SECURE_CERT_NONE, debug=True, username='user', password='password'
        )
        PVOutputAPISettings.objects.update(auth_token=None, system_identifier=None)
        queue.Message.objects.all().delete()
        Notification.objects.update(read=True)
        Notification.objects.create(message='Development reset completed.')

        try:
            # Reset passwd.
            admin = User.objects.get(username='admin')
        except User.DoesNotExist:
            User.objects.create_superuser('admin', 'root@localhost', 'admin')
        else:
            admin.set_password('admin')
            admin.save()
