from django import forms
from django.utils.translation import ugettext_lazy as _

import dsmr_backend.services


class ExportAsCsvForm(forms.Form):
    DATA_TYPE_DAY = 'day'
    DATA_TYPE_HOUR = 'hour'
    DATA_TYPES = (
        (DATA_TYPE_DAY, _('Day')),
        (DATA_TYPE_HOUR, _('Hour')),
    )
    EXPORT_FORMAT_CSV = 'csv'
    EXPORT_FORMATS = (
        (EXPORT_FORMAT_CSV, _('CSV')),
    )

    data_type = forms.ChoiceField(choices=DATA_TYPES)
    start_date = forms.DateField()
    end_date = forms.DateField()
    export_format = forms.ChoiceField(choices=EXPORT_FORMATS)


class DashboardElectricityConsumptionForm(forms.Form):
    delivered = forms.BooleanField(required=False, initial=False)
    returned = forms.BooleanField(required=False, initial=False)
    phases = forms.BooleanField(required=False, initial=False)
    latest_delta_id = forms.IntegerField(required=False, initial=None)

    def __init__(self, *args, **kwargs):
        self.capabilities = dsmr_backend.services.get_capabilities()
        super(DashboardElectricityConsumptionForm, self).__init__(*args, **kwargs)

    def _clean_type(self, field, capability):
        value = self.cleaned_data[field]

        if value and not self.capabilities[capability]:
            raise forms.ValidationError(capability)

        return value

    def clean_delivered(self):
        return self._clean_type('delivered', 'electricity')

    def clean_returned(self):
        return self._clean_type('returned', 'electricity_returned')

    def clean_phases(self):
        return self._clean_type('phases', 'multi_phases')


class DashboardNotificationReadForm(forms.Form):
    notification_id = forms.IntegerField()
