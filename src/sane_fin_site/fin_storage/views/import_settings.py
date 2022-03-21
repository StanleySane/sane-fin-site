import json
import logging
import typing

from django import forms
from django.contrib import messages
from django.contrib.sessions.backends.base import SessionBase
from django.core.files.uploadedfile import UploadedFile
from django.forms import widgets
from django.forms.utils import ErrorList
from django.http import HttpResponseBadRequest
from django.urls import reverse_lazy
from django.views import generic

from .common import all_pages_context, form_as_div
from .. import apps
from ..storage_manage import ImportSettingsItem, ImportSettingsData, SettingsManager


class ImportSettingsForm(forms.Form):
    required_css_class = 'required'
    error_css_class = 'errors'

    settings_file = forms.FileField(required=False)
    settings_items = forms.MultipleChoiceField(widget=widgets.CheckboxSelectMultiple(attrs={'checked': True}),
                                               required=False)

    def __init__(
            self,
            instance=None,
            settings_data: ImportSettingsData = None,
            data=None, files=None, auto_id='id_%s', prefix=None,
            initial=None, error_class=ErrorList, label_suffix=None,
            empty_permitted=False, field_order=None, use_required_attribute=None, renderer=None):
        super().__init__(
            data, files, auto_id, prefix,
            initial, error_class, label_suffix,
            empty_permitted, field_order, use_required_attribute, renderer)

        self.instance = instance
        self.settings_data = settings_data

        if settings_data is not None:
            self.update_settings_data(settings_data)

    def update_settings_data(self, settings_data: ImportSettingsData):
        assert settings_data is not None

        settings_items_field = self.fields['settings_items']
        settings_items_field.choices = [
            (settings_item.exporter_unique_code, settings_item)
            for settings_item
            in settings_data.items
        ]

    def as_div_file_field(self):
        all_fields = self.fields
        try:
            # temporary leave in fields dictionary only needed fields
            self.fields = {field_name: field
                           for field_name, field
                           in self.fields.items()
                           if field_name in 'settings_file'}

            # generate HTML only for needed fields
            html = form_as_div(self)

        finally:
            self.fields = all_fields

        return html

    def save(self):
        return self.instance


class ImportSettingsView(generic.edit.CreateView):
    template_name = f'{apps.FinStorageConfig.name}/import_settings.html'
    form_class = ImportSettingsForm

    settings_data: typing.Optional[ImportSettingsData]
    title = 'Import settings'

    _session_key = 'import_settings'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.save_succeeded = False
        self.settings_data = None
        self.settings_manager = SettingsManager()

    def store_session_info(
            self,
            settings_data: ImportSettingsData) -> None:
        session: SessionBase = self.request.session

        json_data = {
            'file_name': settings_data.file_name,
            'file_content': settings_data.file_content
        }
        json_string = json.dumps(json_data)

        session.set_expiry(0)
        session[self._session_key] = json_string

    def read_session_info(self) -> typing.Optional[ImportSettingsData]:
        session: SessionBase = self.request.session

        json_string = session.get(self._session_key, None)
        if json_string is None:
            return None

        json_data = json.loads(json_string)

        settings_file_name = json_data['file_name']
        settings_file_content = json_data['file_content']

        try:
            settings_data = self.settings_manager.deserialize_settings(
                settings_file_name=settings_file_name,
                settings_file_content=settings_file_content)

        except Exception as ex:
            messages.error(self.request, f"Error of parse file {settings_file_name!r}: {ex}")
            self.drop_session_info()
            return None

        return settings_data

    def drop_session_info(self):
        session: SessionBase = self.request.session

        if self._session_key in session:
            del session[self._session_key]

    def get_success_url(self):
        return reverse_lazy(
            f'{apps.FinStorageConfig.name}:exporters'
            if self.save_succeeded
            else f'{apps.FinStorageConfig.name}:import_settings',
            current_app=apps.FinStorageConfig.name)

    def get_initial(self):
        initial = super().get_initial()

        settings_data = self.read_session_info()
        if settings_data:
            self.settings_data = settings_data
            initial['settings_data'] = settings_data

        self.initial = initial
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        if self.settings_data is not None:
            kwargs.update({
                'settings_data': self.settings_data
            })

        return kwargs

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        context_data.update({
            **all_pages_context(),
            'title': self.title,
            'subtitle': None,
            'has_settings_items': bool(self.settings_data is not None and self.settings_data.items)
        })

        return context_data

    def form_valid(self, form: ImportSettingsForm):
        cleaned_data: dict[str, typing.Any] = form.cleaned_data

        if '_upload' in self.request.POST:
            settings_file: UploadedFile = cleaned_data['settings_file']
            if settings_file is None:
                form.add_error(
                    None,
                    {'settings_file': "Select file to parse"}
                )
                self.drop_session_info()
                return super().form_invalid(form)

            self.logger.info(f"Upload file {settings_file.name!r}")

            try:
                settings_file_content = settings_file.read().decode()
                settings_data = self.settings_manager.deserialize_settings(settings_file.name, settings_file_content)

            except Exception as ex:
                form.add_error(
                    None,
                    {'settings_file': f"Error of parse file {settings_file.name!r}: {ex}"}
                )
                self.drop_session_info()
                return super().form_invalid(form)

            self.store_session_info(settings_data)

            return super().form_valid(form)

        elif '_save' in self.request.POST:
            self.logger.info("Try to save uploaded file")

            if self.settings_data is None:
                form.add_error(
                    None,
                    "Can't save data because setting data not found. Try to parse it again."
                )
                return super().form_invalid(form)

            selected_exporters: typing.Set[str] = set(cleaned_data['settings_items'])
            selected_history_data: typing.Set[str] = set(self.request.POST.getlist('_selected_history_data'))
            selected_downloaded_intervals: typing.Set[str] = set(
                self.request.POST.getlist('_selected_downloaded_intervals'))
            if not selected_exporters and not selected_history_data and not selected_downloaded_intervals:
                messages.info(self.request, "Nothing to save. No items was selected.")
                return self.render_to_response(self.get_context_data(form=form))

            settings_items_by_exporter_code: typing.Dict[str, ImportSettingsItem] = {
                settings_item.exporter_unique_code: settings_item
                for settings_item
                in self.settings_data.items}

            # checkout selected exporters not from parsed settings data
            not_found_exporters = [
                selected_exporter_code
                for selected_exporter_code
                in selected_exporters
                if selected_exporter_code not in settings_items_by_exporter_code
            ]
            if not_found_exporters:
                form.add_error(
                    'settings_items',
                    f"Can't save next exporters because its not found in settings data: "
                    f"{', '.join(not_found_exporters)}. "
                    f"Try to parse file again."
                )
                return super().form_invalid(form)

            # checkout items for the new exporters with no corresponding exporter to save
            items_without_exporter_to_save = [
                selected_data_item
                for selected_data_item
                in selected_history_data | selected_downloaded_intervals
                if (selected_data_item not in selected_exporters
                    and (selected_data_item not in settings_items_by_exporter_code
                         or settings_items_by_exporter_code[selected_data_item].is_new))
            ]
            if items_without_exporter_to_save:
                form.add_error(
                    'settings_items',
                    f"Can't save related data without saving corresponding exporter for the next items: "
                    f"{', '.join(items_without_exporter_to_save)}"
                )
                return super().form_invalid(form)

            self.settings_manager.save_settings_data(
                self.settings_data,
                selected_exporters,
                selected_history_data,
                selected_downloaded_intervals)

            message = f"Settings was saved successfully for the next exporters: "\
                      f"{', '.join(selected_exporters | selected_history_data | selected_downloaded_intervals)}"
            self.logger.info(message)
            messages.success(self.request, message)
            self.drop_session_info()

            self.save_succeeded = True
            return super().form_valid(form)

        else:
            self.logger.error("Bad POST request")
            return HttpResponseBadRequest()
