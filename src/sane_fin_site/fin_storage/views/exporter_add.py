import logging
import typing
import uuid

from django import forms
from django.forms import widgets
from django.forms.utils import ErrorList
from django.urls import reverse_lazy
from django.views import generic
from sane_finances.inspection import analyzers
from sane_finances.sources.base import InstrumentExporterRegistry

from .common import all_pages_context
from .. import apps
from ..cachers import StaticDataCache


class ExporterTypeForm(forms.Form):
    required_css_class = 'required'
    error_css_class = 'errors'

    exporter_type = forms.ChoiceField(widget=widgets.RadioSelect())

    class InstrumentExporterRegistryViewModel:
        def __init__(self, registry: InstrumentExporterRegistry):
            self._registry = registry

        @property
        def name(self):
            return self._registry.name

        @property
        def provider_site(self):
            return self._registry.provider_site

        @property
        def api_url(self):
            return self._registry.api_url

        @property
        def exporter_type(self):
            return analyzers.get_full_path(self._registry.factory.__class__)

    def __init__(self,
                 available_exporters_registries: typing.OrderedDict[int, InstrumentExporterRegistry],
                 instance=None,
                 data=None, files=None, auto_id='id_%s', prefix=None,
                 initial=None, error_class=ErrorList, label_suffix=None,
                 empty_permitted=False, field_order=None, use_required_attribute=None, renderer=None):
        super().__init__(data, files, auto_id, prefix,
                         initial, error_class, label_suffix,
                         empty_permitted, field_order, use_required_attribute, renderer)

        self.available_exporters_registries = available_exporters_registries
        self.instance = instance

        self.fields['exporter_type'].choices = [
            (str(registry_id), ExporterTypeForm.InstrumentExporterRegistryViewModel(registry))
            for registry_id, registry
            in available_exporters_registries.items()]

    @property
    def model_name(self):
        return 'exporter_type'

    def save(self):
        return self.instance


class ExportersAddView(generic.edit.CreateView):
    template_name = apps.FinStorageConfig.name + '/exporter_add.html'
    form_class = ExporterTypeForm

    title = 'Add new exporter'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.selected_type_id: typing.Optional[int] = None

    def get_success_url(self):
        return reverse_lazy(apps.FinStorageConfig.name + ':exporters_add_typed_info',
                            kwargs={'type_id': self.selected_type_id,
                                    'rand_id': uuid.uuid4()},
                            current_app=apps.FinStorageConfig.name)

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        context_data.update(
            {
                **all_pages_context(),
                'title': self.title,
                'subtitle': None
            })
        return context_data

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        available_exporters_registries = StaticDataCache().get_available_exporters_registries()

        self.logger.info(f"Got {len(available_exporters_registries)} available exporters registries")

        kwargs['available_exporters_registries'] = available_exporters_registries

        return kwargs

    def form_valid(self, form: forms.Form):
        self.selected_type_id = int(form.cleaned_data['exporter_type'])

        return super().form_valid(form)
