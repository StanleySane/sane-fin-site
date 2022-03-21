import logging
import typing

from django import forms
from django.contrib import messages
from django.contrib.admin.widgets import AdminTextInputWidget, AdminTextareaWidget
from django.contrib.messages.views import SuccessMessageMixin
from django.contrib.sessions.backends.base import SessionBase
from django.forms import widgets
from django.forms.utils import ErrorList
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse_lazy
from django.views import generic
from sane_finances.inspection import serialize
from sane_finances.sources.base import (
    AnyInstrumentInfoProvider, AnyInstrumentHistoryDownloadParameters,
    InstrumentInfoProvider, InstrumentExporterFactory)

from .common import all_pages_context, form_as_div, SpecificInstanceManagersPack
from .. import apps
from .. import db
from ..cachers import StaticDataCache
from ..form_fields_managers import FormFieldsManager
from ..view_models import Exporter


class ExporterEditForm(forms.Form):
    fields: typing.Dict[str, forms.Field]
    specific_fields_names: typing.Tuple[str, ...]

    required_css_class = 'required'
    error_css_class = 'errors'

    unique_code = forms.CharField(widget=AdminTextInputWidget())
    description = forms.CharField(widget=AdminTextareaWidget({'rows': 2}))
    exporter_type = forms.CharField(widget=AdminTextareaWidget({'cols': 80, 'rows': 1}), disabled=True)
    is_active = forms.BooleanField(required=False)
    instrument_info = forms.ChoiceField(widget=widgets.RadioSelect(), required=False, disabled=True)

    readonly_fields_widgets = {'unique_code': widgets.TextInput(),
                               'description': widgets.Textarea({'rows': 2}),
                               'exporter_type': widgets.Textarea({'cols': 80, 'rows': 1}),
                               'is_active': widgets.CheckboxInput()}

    instrument_info_field_name = 'instrument_info'

    # fields that appear always for any type of instance. must have names matched with exporter attributes
    common_fields_names = ('unique_code', 'description', 'exporter_type', 'is_active')

    def __init__(self,
                 readonly=False,
                 specific_fields: typing.Dict[str, forms.Field] = None,
                 immutable_fields: typing.FrozenSet[str] = None,
                 instrument_info_manual_fields: typing.Dict[str, forms.Field] = None,
                 instance=None,
                 available_instruments: typing.OrderedDict[str, AnyInstrumentInfoProvider] = None,
                 data=None, files=None, auto_id='id_%s', prefix=None,
                 initial=None, error_class=ErrorList, label_suffix=None,
                 empty_permitted=False, field_order=None, use_required_attribute=None, renderer=None):

        assert self.instrument_info_field_name not in self.common_fields_names, \
            "'instrument_info_field_name' has invalid value shared with 'common_fields_names'"

        super().__init__(data, files, auto_id, prefix,
                         initial, error_class, label_suffix,
                         empty_permitted, field_order, use_required_attribute, renderer)

        assert self.fields.keys() == set(self.common_fields_names + (self.instrument_info_field_name,)), \
            "Attributes 'common_fields_names' and/or 'instrument_info_field_name' are not initialized properly"

        if available_instruments is not None:
            self.update_available_instruments(available_instruments)

        new_fields = specific_fields or {}
        self.specific_fields_names = tuple(new_fields.keys())

        for field_name, field in new_fields.items():
            if readonly:
                field.disabled = True
            self.fields[field_name] = field

        manual_fields = instrument_info_manual_fields or {}
        self.instrument_info_manual_fields_names = tuple(manual_fields.keys())

        for field_name, field in manual_fields.items():
            if readonly:
                field.disabled = True
            field.required = False
            self.fields[field_name] = field

        if readonly:
            for field_name, readonly_widget in self.readonly_fields_widgets.items():
                field = self.fields[field_name]
                field.widget = readonly_widget
                field.disabled = True

        if immutable_fields:
            for field_name in immutable_fields:
                field = self.fields[field_name]
                field.disabled = True

        self.instance = instance

    def update_available_instruments(self, available_instruments: typing.OrderedDict[str, AnyInstrumentInfoProvider]):
        if available_instruments is None:
            raise ValueError("'available_instruments' is None")

        instrument_info_field = self.fields[self.instrument_info_field_name]
        instrument_info_field.disabled = False
        instrument_info_field.choices = list(available_instruments.items())

    def as_div_common(self):
        return self._as_div_for_fields(self.common_fields_names)

    def as_div_specific(self):
        return self._as_div_for_fields(self.specific_fields_names)

    def as_div_manual(self):
        return self._as_div_for_fields(self.instrument_info_manual_fields_names)

    def _as_div_for_fields(self, fields_names):
        all_fields = self.fields
        try:
            # temporary leave in fields dictionary only needed fields
            self.fields = {field_name: field
                           for field_name, field
                           in self.fields.items()
                           if field_name in fields_names}

            # generate HTML only for needed fields
            html = form_as_div(self)

        finally:
            self.fields = all_fields

        return html

    @property
    def model_name(self):
        return 'exporter_edit'

    def save(self):
        return self.instance


class SessionFormDataStoreMixin:
    common_session_key_suffix = 'common'
    info_session_key_suffix = 'info'
    params_session_key_suffix = 'params'

    _all_session_key_suffixes = (common_session_key_suffix,
                                 info_session_key_suffix,
                                 params_session_key_suffix)

    def get_session_exporter_key(self) -> str:
        raise NotImplementedError(
            f"{self.__class__.__name__} is missing the implementation of the get_session_exporter_key() method."
        )

    def _get_session(self) -> SessionBase:
        # noinspection PyUnresolvedReferences
        return self.request.session

    def validate_session_key_suffix(self, session_key_suffix: str):
        if session_key_suffix not in self._all_session_key_suffixes:
            raise ValueError(f"Unknown session key suffix: {session_key_suffix!r}")

    def _store_form_data(
            self,
            session_key_suffix: str,
            form_cleaned_data: typing.Dict[str, typing.Any],
            fields_names: typing.Iterator[str],
            form_data_serializer: serialize.FlattenedDataSerializer) -> None:
        self.validate_session_key_suffix(session_key_suffix)

        exporter_key = self.get_session_exporter_key()
        session = self._get_session()

        cleaned_data = {
            form_field_name: field_value
            for form_field_name, field_value
            in form_cleaned_data.items()
            if form_field_name in fields_names}

        json_form_data = form_data_serializer.serialize_flattened_data(cleaned_data)

        session_key = f"exporter_{exporter_key}_{session_key_suffix}"

        session.set_expiry(0)
        session[session_key] = json_form_data

    def store_specific_form_data(
            self,
            session_key_suffix: str,
            form_cleaned_data: typing.Dict[str, typing.Any],
            form_fields_manager: FormFieldsManager,
            form_data_serializer: serialize.FlattenedDataSerializer) -> None:
        specific_fields_names = tuple(form_fields_manager.form_fields.keys())
        self._store_form_data(
            session_key_suffix,
            form_cleaned_data,
            specific_fields_names,
            form_data_serializer)

    def store_common_form_data(
            self,
            form_cleaned_data: typing.Dict[str, typing.Any],
            form: ExporterEditForm,
            form_data_serializer: serialize.FlattenedDataSerializer) -> None:
        self._store_form_data(
            self.common_session_key_suffix,
            form_cleaned_data,
            form.common_fields_names,
            form_data_serializer)

    def _read_form_data(
            self,
            session_key_suffixes: typing.Iterator[str],
            form_data_serializer: serialize.FlattenedDataSerializer) -> typing.Optional[typing.Dict[str, typing.Any]]:
        exporter_key = self.get_session_exporter_key()
        session = self._get_session()

        form_data = {}
        for suffix in session_key_suffixes:
            session_key = f"exporter_{exporter_key}_{suffix}"
            json_form_data = session.get(session_key, None)
            if json_form_data is not None:
                cleaned_data = form_data_serializer.deserialize_flattened_data(
                    json_form_data,
                    decode_dynamic_enums=False)

                assert not (form_data.keys() & cleaned_data.keys()), \
                    "Specific, common and/or dynamic fields has shared items"

                form_data.update(cleaned_data)

        return form_data or None

    def read_form_data(
            self,
            session_key_suffix: str,
            form_data_serializer: serialize.FlattenedDataSerializer) -> typing.Optional[typing.Dict[str, typing.Any]]:
        self.validate_session_key_suffix(session_key_suffix)

        return self._read_form_data((session_key_suffix, self.common_session_key_suffix), form_data_serializer)

    def read_specific_form_data(
            self,
            session_key_suffix: str,
            form_data_serializer: serialize.FlattenedDataSerializer) -> typing.Optional[typing.Dict[str, typing.Any]]:
        self.validate_session_key_suffix(session_key_suffix)

        return self._read_form_data((session_key_suffix,), form_data_serializer)

    def drop_form_data(self) -> None:
        exporter_key = self.get_session_exporter_key()
        session = self._get_session()

        for suffix in self._all_session_key_suffixes:
            session_key = f"exporter_{exporter_key}_{suffix}"
            if session_key in session:
                del session[session_key]


class UpdateExporterViewMixin:
    """ Mixin for view of update existing exporter
    """
    kwargs: typing.Dict[str, typing.Any]

    def get_session_exporter_key(self) -> str:
        return str(self.kwargs.get('id'))

    def get_exporter(self) -> Exporter:
        pk = self.kwargs.get('id')

        # If not defined, it's an error.
        if pk is None:
            raise ValueError(
                f"Detail view {self.__class__.__name__} must be called with an object id "
                "in the URLconf."
            )

        exporter = db.DatabaseContext().get_exporter_by_id(pk)
        return exporter


class CreateExporterViewMixin:
    """ Mixin for view of create new exporter
    """
    kwargs: typing.Dict[str, typing.Any]

    def get_reverse_kwargs(self) -> typing.Dict[str, typing.Any]:
        return {'type_id': self.kwargs.get('type_id'), 'rand_id': self.kwargs.get('rand_id')}

    def get_session_exporter_key(self) -> str:
        return str(self.kwargs.get('rand_id'))

    def get_exporter(self) -> Exporter:
        type_id = self.kwargs.get('type_id')
        # If not defined, it's an error.
        if type_id is None:
            raise ValueError(
                f"Edit view {self.__class__.__name__} must be called with an exporter type id "
                "in the URLconf."
            )

        rand_id = self.kwargs.get('rand_id')
        # If not defined, it's an error.
        if rand_id is None:
            raise ValueError(
                f"Edit view {self.__class__.__name__} must be called with exporter id fake hash "
                "in the URLconf."
            )

        available_exporters_registries = StaticDataCache().get_available_exporters_registries()
        if type_id not in available_exporters_registries:
            raise ValueError(f"Exporter with type id {type_id!r} not found")

        exporter_registry = available_exporters_registries[type_id]

        # noinspection PyTypeChecker
        exporter = Exporter(
            id=None,
            unique_code='',
            description='',
            is_active=True,
            exporter_registry=exporter_registry,
            download_info_parameters=None,
            download_info_parameters_str=None,
            download_history_parameters=None,
            download_history_parameters_str=None,
            history_data={},
            downloaded_intervals=[]
        )

        return exporter


class BaseExportersEditView(SessionFormDataStoreMixin, generic.edit.UpdateView):
    template_name = apps.FinStorageConfig.name + '/exporter_edit.html'
    form_class = ExporterEditForm

    object: Exporter
    title: str

    # instance managers:
    info_params_managers: SpecificInstanceManagersPack
    history_params_managers: SpecificInstanceManagersPack

    # view specific attributes:
    session_key_suffix: str
    find_instrument: bool
    can_cancel = False
    new_exporter = False
    validate_exporter_availability = True

    def init_instance_managers(self, instrument_exporter_factory: InstrumentExporterFactory):
        download_param_values_storage = StaticDataCache().download_parameter_values_storage(
            instrument_exporter_factory)

        self.info_params_managers = SpecificInstanceManagersPack(
            download_param_values_storage,
            instrument_exporter_factory.download_parameters_factory.download_info_parameters_class,
            instrument_exporter_factory.download_parameters_factory.download_info_parameters_factory,
            'info')
        self.history_params_managers = SpecificInstanceManagersPack(
            download_param_values_storage,
            instrument_exporter_factory.download_parameters_factory.download_history_parameters_class,
            instrument_exporter_factory.download_parameters_factory.download_history_parameters_factory,
            'data')

    def build_download_history_params_instance(
            self,
            form_data: typing.Dict[str, typing.Any]) -> AnyInstrumentHistoryDownloadParameters:
        params_managers = self.history_params_managers
        form_data = params_managers.form_fields_manager.materialize_choice_values(form_data)
        download_history_params_factory_data = \
            params_managers.factory_converter.get_instance_factory_data(form_data)
        download_history_parameters = params_managers.instance_builder.build_instance(
            download_history_params_factory_data)

        assert isinstance(
            download_history_parameters,
            self.object.exporter_registry.factory.download_parameters_factory.download_history_parameters_class)

        return download_history_parameters

    def build_download_info_params_instance(self, form_data: typing.Dict[str, typing.Any]):
        params_managers = self.info_params_managers
        form_data = params_managers.form_fields_manager.materialize_choice_values(form_data)
        info_params_factory_data = \
            params_managers.factory_converter.get_instance_factory_data(form_data)
        download_info_parameters = \
            params_managers.instance_builder.build_instance(info_params_factory_data)

        assert isinstance(
            download_info_parameters,
            self.object.exporter_registry.factory.download_parameters_factory.download_info_parameters_class)

        return download_info_parameters

    def get_session_exporter_key(self) -> str:
        return super().get_session_exporter_key()

    def get_exporter(self) -> Exporter:
        raise NotImplementedError(
            f"{self.__class__.__name__} is missing the implementation of the get_exporter() method."
        )

    def get_object(self, queryset=None) -> Exporter:
        exporter = self.get_exporter()
        if self.validate_exporter_availability and exporter.disabled:
            raise ValueError(f"Exporter with id {exporter.id} is not available for edit")

        self.init_instance_managers(exporter.exporter_registry.factory)

        return exporter

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        context_data.update({
            **all_pages_context(),
            'find_instrument': self.find_instrument,
            'can_cancel': self.can_cancel,
            'new_exporter': self.new_exporter,
            'title': self.title,
            'subtitle': None
        })

        return context_data

    def get_instance_for_form_fields_data(self):
        raise NotImplementedError()

    def get_specific_instance_managers(self) -> SpecificInstanceManagersPack:
        raise NotImplementedError()

    def get_initial(self):
        initial = super().get_initial()

        specific_instance_managers = self.get_specific_instance_managers()

        stored_form_data = self.read_form_data(
            self.session_key_suffix,
            specific_instance_managers.serializer) or {}

        initial.update({field_name: getattr(self.object, field_name)
                        for field_name
                        in self.form_class.common_fields_names})

        initial.update(specific_instance_managers.instance_flattener.get_flattened_data_from(
            self.get_instance_for_form_fields_data()))

        # rewrite initial data with data stored in session
        initial.update(stored_form_data)

        self.initial = initial
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        form_fields_manager = self.get_specific_instance_managers().form_fields_manager

        specific_fields = form_fields_manager.form_fields
        immutable_fields = form_fields_manager.immutable_form_fields

        kwargs.update({
            'specific_fields': specific_fields,
            'immutable_fields': immutable_fields
        })

        return kwargs


class ExportersEditInfoView(UpdateExporterViewMixin, BaseExportersEditView):
    session_key_suffix = SessionFormDataStoreMixin.info_session_key_suffix
    find_instrument = True
    title = 'Edit exporter'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.finish_edit = False
        self.static_data_cache = StaticDataCache()
        self.database_context = db.DatabaseContext()

    def get_instance_for_form_fields_data(self):
        return self.object.download_info_parameters

    def get_specific_instance_managers(self) -> SpecificInstanceManagersPack:
        return self.info_params_managers

    def get_available_instruments_cache_key(
            self,
            form_data: typing.Dict[str, typing.Any],
            field_names: typing.Iterator[str]) -> typing.Tuple[str, ...]:
        available_instruments_cache_key = (self.object.exporter_type,) + tuple(
            form_data.get(form_field_name, None)
            for form_field_name
            in field_names)
        return available_instruments_cache_key

    def get_success_url(self):
        return reverse_lazy(
            (apps.FinStorageConfig.name + ':exporters_edit_params'
             if self.finish_edit
             else apps.FinStorageConfig.name + ':exporters_edit_info'),
            kwargs={'id': self.object.id},
            current_app=apps.FinStorageConfig.name)

    def get_initial(self):
        initial = super().get_initial()

        instrument_identity_form_fields = \
            self.history_params_managers.form_fields_manager.instrument_identity_form_fields

        # add manual fields to specific fields
        stored_form_data = self.read_form_data(
            ExportersEditParamsView.session_key_suffix,
            self.history_params_managers.serializer) or {}

        history_params_data = self.history_params_managers.instance_flattener.get_flattened_data_from(
            self.object.download_history_parameters)

        # update initial with data from db
        initial.update({field_name: field_value
                        for field_name, field_value
                        in history_params_data.items()
                        if field_name in instrument_identity_form_fields})

        # rewrite initial data with data stored in session
        initial.update({field_name: field_value
                        for field_name, field_value
                        in stored_form_data.items()
                        if field_name in instrument_identity_form_fields})

        self.initial = initial
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        # prepare manual data block
        instrument_identity_form_fields = \
            self.history_params_managers.form_fields_manager.instrument_identity_form_fields
        kwargs.update({
            'instrument_info_manual_fields': {
                field_name: form_field
                for field_name, form_field
                in self.history_params_managers.form_fields_manager.form_fields.items()
                if field_name in instrument_identity_form_fields
            }
        })

        # try to read from internal cache after success 'Find'
        available_instruments_cache_key = self.get_available_instruments_cache_key(
            self.initial,
            tuple(self.info_params_managers.form_fields_manager.form_fields.keys())
        )
        cached_instruments = self.static_data_cache.get_available_instruments(available_instruments_cache_key)
        if cached_instruments is not None:
            # fill list of found available instruments
            kwargs.update({
                'available_instruments': cached_instruments or None
            })

        return kwargs

    def form_valid(self, form: ExporterEditForm):
        cleaned_data: dict[str, typing.Any] = form.cleaned_data

        unique_code = cleaned_data.get('unique_code')
        if unique_code and not self.database_context.is_exporter_code_unique(unique_code, self.object.id):
            form.add_error('unique_code', "Exporter code is not unique")
            return super().form_invalid(form)

        available_instruments_cache_key = self.get_available_instruments_cache_key(
            cleaned_data,
            form.specific_fields_names
        )
        instrument_identity_form_fields = \
            self.history_params_managers.form_fields_manager.instrument_identity_form_fields

        specific_cleaned_data = {
            form_field_name: field_value
            for form_field_name, field_value
            in cleaned_data.items()
            if form_field_name in form.specific_fields_names}
        info_params = self.build_download_info_params_instance(specific_cleaned_data)

        if '_find' in self.request.POST:
            self.logger.info(f"Try to find available instruments of {self.object.unique_code!r} "
                             f"by parameters {info_params}")

            # download anyway even if it's in cache
            _ = self.static_data_cache.download_available_instruments(
                available_instruments_cache_key,
                info_params,
                self.object.exporter_registry.factory)

            self.store_common_form_data(cleaned_data, form, self.info_params_managers.serializer)
            self.store_specific_form_data(
                self.session_key_suffix,
                cleaned_data,
                self.info_params_managers.form_fields_manager,
                self.info_params_managers.serializer)

            self.finish_edit = False
            return super().form_valid(form)

        elif '_with_found' in self.request.POST:
            self.logger.info("Continue editing info with found instrument")

            # check if specific fields not changed
            changed_specific_fields_names = tuple(set(form.changed_data) & set(form.specific_fields_names))
            if changed_specific_fields_names:
                form.add_error(
                    None,
                    {changed_field_name: "Can't continue because of changing this field. "
                                         "You have to 'Find' and select appropriate instrument first."
                     for changed_field_name
                     in changed_specific_fields_names}
                )
                return super().form_invalid(form)

            # check if any instrument was selected (since it not required in the form)
            instrument_info_code = cleaned_data[form.instrument_info_field_name]
            if not instrument_info_code:
                form.add_error(form.instrument_info_field_name, "You should select one of the instruments")
                return super().form_invalid(form)

            self.logger.info(f"Found instrument code: {instrument_info_code}")

            # get all available instruments
            all_available_instruments = self.static_data_cache.get_available_instruments(
                available_instruments_cache_key)
            if all_available_instruments is None:
                all_available_instruments = self.static_data_cache.download_available_instruments(
                    available_instruments_cache_key,
                    info_params,
                    self.object.exporter_registry.factory)

            if instrument_info_code not in all_available_instruments:
                form.add_error(
                    form.instrument_info_field_name,
                    f"Not found instrument with code {instrument_info_code!r} in available instruments. "
                    f"Try to find another instrument."
                )
                return super().form_invalid(form)

            instrument_info = all_available_instruments[instrument_info_code]

        elif '_with_manual' in self.request.POST:
            self.logger.info("Continue editing info with manual identity")

            # check if all manual fields were filled (since they are not required in the form)
            empty_fields = [identity_field_name
                            for identity_field_name
                            in instrument_identity_form_fields
                            if not cleaned_data.get(identity_field_name, None)]
            if empty_fields:
                form.add_error(None, {field_name: "Field is required" for field_name in empty_fields})
                return super().form_invalid(form)

            instrument_info: typing.Optional[InstrumentInfoProvider] = None

        else:
            self.logger.error("Bad POST request")
            return HttpResponseBadRequest()

        self.store_common_form_data(cleaned_data, form, self.info_params_managers.serializer)
        self.store_specific_form_data(
            self.session_key_suffix,
            cleaned_data,
            self.info_params_managers.form_fields_manager,
            self.info_params_managers.serializer)

        # try to read download history parameters from session
        session_history_params_form_data = self.read_specific_form_data(
            ExportersEditParamsView.session_key_suffix,
            self.history_params_managers.serializer)
        if session_history_params_form_data is not None:
            download_history_parameters = \
                self.build_download_history_params_instance(session_history_params_form_data)

        else:
            # if not found in session then take instance from DB
            download_history_parameters = self.object.download_history_parameters

        # change some attributes in download history parameters based on changed download info parameters
        download_history_parameters = \
            self.object.exporter_registry.factory.download_parameters_factory \
                .generate_history_download_parameters_from(download_history_parameters,
                                                           info_params,
                                                           instrument_info)
        history_params_form_data = \
            self.history_params_managers.instance_flattener.get_flattened_data_from(download_history_parameters)

        if '_with_manual' in self.request.POST:
            # update data to save in session with data from manual fields
            manual_fields_data = {
                field_name: field_value
                for field_name, field_value
                in cleaned_data.items()
                if field_name in instrument_identity_form_fields
            }
            self.logger.info(f"Manual identity: {manual_fields_data}")
            history_params_form_data.update(manual_fields_data)

        self.store_specific_form_data(
            ExportersEditParamsView.session_key_suffix,
            history_params_form_data,
            self.history_params_managers.form_fields_manager,
            self.history_params_managers.serializer)

        self.finish_edit = True
        self.logger.info("Instrument info editing finished")
        return super().form_valid(form)


class ExportersAddTypedInfoView(CreateExporterViewMixin, ExportersEditInfoView):
    can_cancel = True
    new_exporter = True
    title = 'Add new exporter'

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        context_data.update(self.get_reverse_kwargs())
        return context_data

    def get_success_url(self):
        return reverse_lazy(
            (apps.FinStorageConfig.name + ':exporters_add_typed_params'
             if self.finish_edit
             else apps.FinStorageConfig.name + ':exporters_add_typed_info'),
            kwargs=self.get_reverse_kwargs(),
            current_app=apps.FinStorageConfig.name)


class ExportersEditParamsView(UpdateExporterViewMixin, BaseExportersEditView):
    session_key_suffix = SessionFormDataStoreMixin.params_session_key_suffix
    find_instrument = False
    title = 'Edit exporter'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.finish_edit = False
        self.database_context = db.DatabaseContext()

    def get_instance_for_form_fields_data(self):
        return self.object.download_history_parameters

    def get_specific_instance_managers(self) -> SpecificInstanceManagersPack:
        return self.history_params_managers

    def get_success_url(self):
        return reverse_lazy(
            (apps.FinStorageConfig.name + ':exporters_detail'
             if self.finish_edit
             else apps.FinStorageConfig.name + ':exporters_edit_info'),
            kwargs={'id': self.object.id},
            current_app=apps.FinStorageConfig.name)

    def form_valid(self, form: ExporterEditForm):
        cleaned_data: dict[str, typing.Any] = form.cleaned_data

        unique_code = cleaned_data.get('unique_code')
        if unique_code and not self.database_context.is_exporter_code_unique(unique_code, self.object.id):
            form.add_error('unique_code', "Exporter code is not unique")
            return super().form_invalid(form)

        if '_save' in self.request.POST:
            self.logger.info("Try to save download parameters")

            self.save(form)

            self.drop_form_data()

            self.finish_edit = True
            return super().form_valid(form)

        elif '_select_another' in self.request.POST:
            self.logger.info("Go to select another instrument")

            self.store_common_form_data(cleaned_data, form, self.history_params_managers.serializer)
            self.store_specific_form_data(
                self.session_key_suffix,
                cleaned_data,
                self.history_params_managers.form_fields_manager,
                self.history_params_managers.serializer)

            self.finish_edit = False
            return super().form_valid(form)

        else:
            self.logger.error("Bad POST request")
            return HttpResponseBadRequest()

    def get_data_to_save(self, form: ExporterEditForm) -> typing.Dict[str, typing.Any]:
        data_to_save = {}

        cleaned_data: dict[str, typing.Any] = form.cleaned_data

        common_cleaned_data = {
            form_field_name: field_value
            for form_field_name, field_value
            in cleaned_data.items()
            if form_field_name in form.common_fields_names and not form.fields[form_field_name].disabled}

        data_to_save.update(common_cleaned_data)

        specific_cleaned_data = {
            form_field_name: field_value
            for form_field_name, field_value
            in cleaned_data.items()
            if form_field_name in form.specific_fields_names}

        download_history_parameters = self.build_download_history_params_instance(specific_cleaned_data)
        data_to_save.update({'download_history_parameters': download_history_parameters})

        # other data may be stored in session
        # otherwise (no data in session) such data not needed to be saved (since it not changed)

        info_params_form_data = self.read_specific_form_data(
            ExportersEditInfoView.session_key_suffix,
            self.info_params_managers.serializer)
        if info_params_form_data is not None:
            download_info_parameters = self.build_download_info_params_instance(info_params_form_data)
            data_to_save.update({'download_info_parameters': download_info_parameters})

        return data_to_save

    def save(self, form: ExporterEditForm):
        data_to_update = self.get_data_to_save(form)
        self.database_context.update_exporter(self.object.id, True, **data_to_update)


class ExportersAddTypedParamsView(CreateExporterViewMixin, ExportersEditParamsView):
    can_cancel = True
    new_exporter = True
    title = 'Add new exporter'

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        context_data.update(self.get_reverse_kwargs())
        return context_data

    def get_success_url(self):
        return (
            reverse_lazy(
                apps.FinStorageConfig.name + ':exporters_detail',
                kwargs={'id': self.object.id},
                current_app=apps.FinStorageConfig.name)
            if self.finish_edit
            else reverse_lazy(
                apps.FinStorageConfig.name + ':exporters_add_typed_info',
                kwargs=self.get_reverse_kwargs(),
                current_app=apps.FinStorageConfig.name)
        )

    def save(self, form: ExporterEditForm):
        data_to_create = self.get_data_to_save(form)
        self.object.id = self.database_context.create_exporter(self.object.exporter_registry, True, **data_to_create)


class DummyForm(forms.Form):
    pass


class ExportersAddTypedCancelView(CreateExporterViewMixin,
                                  SessionFormDataStoreMixin,
                                  SuccessMessageMixin,
                                  generic.edit.FormView):
    template_name = apps.FinStorageConfig.name + '/confirmation.html'
    form_class = DummyForm
    title = 'Are you sure?'

    def get_success_url(self):
        return reverse_lazy(
            apps.FinStorageConfig.name + ':exporters',
            current_app=apps.FinStorageConfig.name)

    def get_success_message(self, cleaned_data):
        return "Creation of new exporter successfully canceled"

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        context_data.update(
            {
                **all_pages_context(),
                'question': "Are you sure you want to cancel creation of the new exporter?",
                'title': self.title,
                'subtitle': None
            })
        return context_data

    def form_valid(self, form):
        self.drop_form_data()

        return super().form_valid(form)


class ExportersDeleteView(UpdateExporterViewMixin,
                          SessionFormDataStoreMixin,
                          SuccessMessageMixin,
                          generic.edit.DeleteView):
    template_name = apps.FinStorageConfig.name + '/confirmation.html'

    object: Exporter

    validate_exporter_availability = False
    title = 'Are you sure?'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

    def get_success_url(self):
        return reverse_lazy(
            apps.FinStorageConfig.name + ':exporters',
            current_app=apps.FinStorageConfig.name)

    @property
    def exporter_description(self):
        return f"[id={self.object.id}, " \
               f"unique_code={self.object.unique_code}, " \
               f"description={self.object.description}, " \
               f"exporter_type={self.object.exporter_type}]"

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)
        context_data.update(
            {
                **all_pages_context(),
                'question': f"Are you sure you want to delete exporter {self.exporter_description}?",
                'title': self.title,
                'subtitle': None
            })
        return context_data

    def get_success_message(self, cleaned_data):
        return f"The exporter {self.exporter_description} was deleted successfully."

    def get_object(self, queryset=None) -> Exporter:
        exporter = self.get_exporter()
        return exporter

    def form_valid(self, form):
        self.object = self.get_object()
        self.logger.info(f"Deleting {self.object.unique_code!r}")
        success_message = self.get_success_message(None)

        db.DatabaseContext().delete_exporter(self.object.id)

        messages.success(self.request, success_message)
        success_url = self.get_success_url()
        return HttpResponseRedirect(success_url)
