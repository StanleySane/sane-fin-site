import datetime
import decimal
import json
import logging
import typing

from django import forms
from django.contrib import messages
from django.contrib.admin.widgets import AdminSplitDateTime
from django.contrib.sessions.backends.base import SessionBase
from django.forms import widgets
from django.forms.utils import ErrorList
from django.http import HttpResponseBadRequest
from django.urls import reverse_lazy
from django.views import generic
from sane_finances.inspection import analyzers
from sane_finances.sources.base import InstrumentValue, InstrumentExporterFactory

from .common import all_pages_context, form_as_div, java_script_date_str
from .exporter_edit import ExporterEditForm
from .. import apps
from .. import db
from ..cachers import StaticDataCache
from ..form_fields_managers import FormFieldsManager, ReadonlyFormFieldsManager
from ..pagination import Pagination
from ..view_models import HistoryDataItem, Exporter


class ExporterDownloadForm(forms.Form):
    required_css_class = 'required'
    error_css_class = 'errors'

    moment_from = forms.SplitDateTimeField(widget=AdminSplitDateTime())
    moment_to = forms.SplitDateTimeField(widget=AdminSplitDateTime())
    history = forms.MultipleChoiceField(widget=widgets.CheckboxSelectMultiple(attrs={'checked': True}),
                                        required=False)

    def __init__(self,
                 history_data: typing.Optional[typing.List[HistoryDataItem]] = None,
                 instance=None,
                 data=None, files=None, auto_id='id_%s', prefix=None,
                 initial=None, error_class=ErrorList, label_suffix=None,
                 empty_permitted=False, field_order=None, use_required_attribute=None, renderer=None):
        super().__init__(data, files, auto_id, prefix,
                         initial, error_class, label_suffix,
                         empty_permitted, field_order, use_required_attribute, renderer)

        self.instance = instance

        if history_data is not None:
            self.update_history_data(history_data)

    def update_history_data(self, history_data: typing.List[HistoryDataItem]):
        if history_data is None:
            raise ValueError("'history_data' is None")

        history_field = self.fields['history']
        history_field.choices = [
            (history_item.moment.isoformat(), history_item)
            for history_item
            in history_data
        ]

    # noinspection PyMethodMayBeStatic
    def history_choices_to_python(self, history_choices: typing.List) -> typing.List:
        return [datetime.datetime.fromisoformat(history_choice) for history_choice in history_choices]

    def as_div(self):
        all_fields = self.fields
        try:
            # temporary leave in fields dictionary only needed fields
            self.fields = {field_name: field
                           for field_name, field
                           in self.fields.items()
                           if field_name != 'history'}

            # generate HTML only for needed fields
            html = form_as_div(self)

        finally:
            self.fields = all_fields

        return html

    @property
    def model_name(self):
        return 'exporter_download'

    def save(self):
        return self.instance


class ExportersDetailView(generic.edit.UpdateView):
    template_name = apps.FinStorageConfig.name + '/exporter_detail.html'
    form_class = ExporterDownloadForm
    exporter_form_class = ExporterEditForm

    object: Exporter
    title = 'Download data for exporter'

    page_parameter_name = 'p'
    all_parameter_name = 'all'

    # instance managers:
    history_params_form_fields_manager: FormFieldsManager
    history_params_flattener: analyzers.InstanceFlattener

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)
        self.history_data: typing.List[InstrumentValue] = []

    def init_instance_managers(self, instrument_exporter_factory: InstrumentExporterFactory):
        download_param_values_storage = StaticDataCache().download_parameter_values_storage(
            instrument_exporter_factory)
        instance_analyzer = analyzers.FlattenedAnnotatedInstanceAnalyzer(
            instrument_exporter_factory.download_parameters_factory.download_history_parameters_class,
            download_param_values_storage,
            'data')
        self.history_params_flattener = analyzers.InstanceFlattener(instance_analyzer, download_param_values_storage)
        self.history_params_form_fields_manager = ReadonlyFormFieldsManager(
            instance_analyzer,
            download_param_values_storage)

    def get_session_exporter_key(self) -> str:
        return str(self.kwargs.get('id'))

    def store_session_info(
            self,
            moment_from: datetime.datetime,
            moment_to: datetime.datetime) -> None:
        session: SessionBase = self.request.session

        exporter_key = self.get_session_exporter_key()
        json_data = {'moment_from': moment_from.isoformat(), 'moment_to': moment_to.isoformat()}
        json_string = json.dumps(json_data)

        session_key = f"exporter_{exporter_key}_download"
        session.set_expiry(0)
        session[session_key] = json_string

    def read_session_info(self) -> typing.Optional[typing.Dict[str, typing.Any]]:
        exporter_key = self.get_session_exporter_key()
        session: SessionBase = self.request.session

        session_key = f"exporter_{exporter_key}_download"
        json_string = session.get(session_key, None)
        if json_string is None:
            return None

        logging.debug(f"Got {json_string} from session for exporter {exporter_key}")

        json_data = json.loads(json_string)

        for key in ('moment_from', 'moment_to'):
            value = json_data.get(key, None)
            if value is not None:
                json_data[key] = datetime.datetime.fromisoformat(value)

        return json_data

    def drop_session_info(self):
        exporter_key = self.get_session_exporter_key()
        session: SessionBase = self.request.session

        session_key = f"exporter_{exporter_key}_download"
        if session_key in session:
            del session[session_key]

    def get_success_url(self):
        return reverse_lazy(
            apps.FinStorageConfig.name + ':exporters_detail',
            kwargs={'id': self.object.id},
            current_app=apps.FinStorageConfig.name)

    def get_object(self, queryset=None) -> Exporter:
        pk = self.kwargs.get('id')

        # If not defined, it's an error.
        if pk is None:
            raise ValueError(
                f"Detail view {self.__class__.__name__} must be called with an object id "
                "in the URLconf."
            )

        exporter = db.DatabaseContext().get_exporter_by_id(pk)

        if not exporter.disabled:
            self.init_instance_managers(exporter.exporter_registry.factory)

        return exporter

    def _get_adjusted_form_history_data(
            self,
            moment_from: datetime.datetime,
            moment_to: datetime.datetime,
            history_values: typing.Iterable[InstrumentValue]) -> typing.List[HistoryDataItem]:

        old_data: typing.Dict[datetime.datetime, InstrumentValue] = \
            {moment: instrument_value
             for moment, instrument_value
             in self.object.history_data.items()
             if moment_from <= moment <= moment_to}

        downloaded_data = {}
        for history_value in history_values:
            moment = history_value.moment
            value = history_value.value

            comment = ""

            prev_value = self.object.history_data.get(moment, None)
            if prev_value is None:
                comment = "New value"

            elif prev_value.value != value:
                comment = "Changed value"

            downloaded_data[moment] = HistoryDataItem(
                moment=moment,
                value=value,
                comment=comment)

        for moment, old_value in old_data.items():
            if moment not in downloaded_data:
                downloaded_data[moment] = HistoryDataItem(
                    moment=moment,
                    value=old_value.value,
                    comment="Missed value",
                    disabled=True)

        history_data = list(downloaded_data.values())
        history_data.sort(key=lambda it: it.moment)
        return history_data

    def get_form_history_data_from_cache(
            self,
            moment_from: datetime.datetime,
            moment_to: datetime.datetime) -> typing.Optional[typing.List[HistoryDataItem]]:
        cached_history_data = StaticDataCache().get_history_data(
            self.object,
            moment_from,
            moment_to)
        if cached_history_data is None:
            return None

        return self._get_adjusted_form_history_data(moment_from, moment_to, cached_history_data)

    def get_initial(self):
        initial = super().get_initial()

        session_info = self.read_session_info()
        if session_info:
            moment_from = session_info.get('moment_from', None)
            if moment_from is not None:
                initial['moment_from'] = moment_from

            moment_to = session_info.get('moment_to', None)
            if moment_to is not None:
                initial['moment_to'] = moment_to

        self.initial = initial
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        moment_from = self.initial.get('moment_from', None)
        moment_to = self.initial.get('moment_to', None)

        if moment_from is not None and moment_to is not None:
            cached_form_history_data = self.get_form_history_data_from_cache(moment_from, moment_to)
            if cached_form_history_data is not None:
                self.history_data = cached_form_history_data
                kwargs.update({
                    'history_data': cached_form_history_data
                })

        return kwargs

    @staticmethod
    def chart_data_str_from_history(history_data: typing.Optional[typing.List[InstrumentValue]]) -> str:
        if not history_data:
            return ""

        history_data.sort(key=lambda v: v.moment)

        chart_data_str = ','.join([
            f"{{x:{java_script_date_str(history_item.moment)},"
            f"y:{history_item.value}}}"
            for history_item
            in history_data])
        return f"[{chart_data_str}]"

    @staticmethod
    def chart_data_str_from_intervals(
            downloaded_intervals: typing.Optional[typing.List[typing.Tuple[datetime.date, datetime.date]]],
            value: decimal.Decimal) -> str:
        """
        {x: new Date(2000, 1, 1), y: null},
        {x: new Date(2000, 1, 1), y: 20}, {x: new Date(2001, 1, 1), y: 18},
        """
        if not downloaded_intervals:
            return ""

        downloaded_intervals.sort(key=lambda it: it[0])

        chart_data_str = ','.join([
            f"{{x:{java_script_date_str(date_from)},y:null}},"
            f"{{x:{java_script_date_str(date_from)},y:{value}}},"
            f"{{x:{java_script_date_str(date_to)},y:{value}}}"
            for date_from, date_to
            in downloaded_intervals])
        return f"[{chart_data_str}]"

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        try:
            page_num = int(self.request.GET.get(self.page_parameter_name, 1))
        except ValueError:
            page_num = 1

        show_all = self.all_parameter_name in self.request.GET

        if 'exporter_form' not in context_data:
            initial = {common_field_name: getattr(self.object, common_field_name)
                       for common_field_name
                       in self.exporter_form_class.common_fields_names}

            if not self.object.disabled:
                initial_download_history_parameters = self.history_params_flattener.get_flattened_data_from(
                    self.object.download_history_parameters)
                history_params_form_fields = self.history_params_form_fields_manager.form_fields

                initial.update(initial_download_history_parameters)

            else:
                history_params_form_fields = {}

            exporter_form = self.exporter_form_class(
                readonly=True,
                specific_fields=history_params_form_fields,
                instance=self.object,
                initial=initial
            )

            context_data['exporter_form'] = exporter_form

        context_data['has_history_data'] = bool(self.history_data)

        form: ExporterDownloadForm = context_data.get('form', None)
        if form is None:
            history_data = []
        else:
            history_data = form['history']

        pagination = Pagination(history_data, page_num, show_all)

        context_data.update({
            **all_pages_context(),
            'chart_downloaded_data': self.chart_data_str_from_history(self.history_data),
            'chart_stored_data': self.chart_data_str_from_history(list(self.object.history_data.values())),
            'chart_intervals_data': self.chart_data_str_from_intervals(
                self.object.downloaded_intervals,
                min((history_item.value
                     for history_item
                     in self.object.history_data.values()), default=decimal.Decimal(0))),
            'pagination': pagination,
            'page_parameter_name': self.page_parameter_name,
            'all_parameter_name': self.all_parameter_name,
            'title': self.title,
            'subtitle': None
        })

        return context_data

    def form_valid(self, form: ExporterDownloadForm):
        cleaned_data: dict[str, typing.Any] = form.cleaned_data
        self.logger.debug(f"Got cleaned data: {cleaned_data}")
        moment_from, moment_to = cleaned_data['moment_from'], cleaned_data['moment_to']

        if '_download' in self.request.POST:
            self.logger.info("Try to download data")

            if moment_from > moment_to:
                form.add_error(
                    None,
                    {'moment_from': "Moment from is greater then moment to",
                     'moment_to': "Moment from is greater then moment to"}
                )
                return super().form_invalid(form)

            _ = StaticDataCache().download_history_data(
                self.object,
                moment_from,
                moment_to)

            self.store_session_info(moment_from, moment_to)

            return super().form_valid(form)

        elif '_save' in self.request.POST:
            self.logger.info("Try to save data")

            history_choices = cleaned_data['history']
            if not history_choices:
                messages.info(self.request, "Nothing to save. No items was selected.")
                return self.render_to_response(self.get_context_data(form=form))

            cached_history_data = StaticDataCache().get_history_data(
                self.object,
                moment_from,
                moment_to)
            if cached_history_data is None:
                form.add_error(
                    None,
                    "Can't save data because downloaded data not found. Try to download it again."
                )
                return super().form_invalid(form)

            cached_history_data = {instrument_value.moment: instrument_value
                                   for instrument_value
                                   in cached_history_data}
            history_keys: typing.List[datetime.datetime] = form.history_choices_to_python(history_choices)
            not_found_keys = []
            history_data_to_save = []
            for history_key in history_keys:
                if history_key not in cached_history_data:
                    not_found_keys.append(history_key)
                else:
                    history_data_to_save.append(cached_history_data[history_key])

            if not_found_keys:
                form.add_error(
                    'history',
                    f"Can't save data because next items not found in downloaded data: "
                    f"{', '.join(map(str, not_found_keys))}"
                )
                return super().form_invalid(form)

            db.DatabaseContext().save_history_data(
                self.object.id,
                history_data_to_save,
                moment_from.date(),
                moment_to.date())

            messages.success(self.request, "History data was saved successfully.")

            StaticDataCache().drop_history_data_from_cache(self.object, moment_from, moment_to)
            self.drop_session_info()

            return super().form_valid(form)

        else:
            self.logger.error("Bad POST request")
            return HttpResponseBadRequest()
