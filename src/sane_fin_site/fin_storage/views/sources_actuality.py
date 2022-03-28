import datetime
import logging
import sys
import typing

from django import forms
from django.contrib import messages
from django.forms import widgets
from django.forms.utils import ErrorList
from django.http import HttpResponseNotFound
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import generic
from sane_finances.communication.cachers import DummyCacher
from sane_finances.communication.url_downloader import UrlDownloader
from sane_finances.inspection import analyzers
from sane_finances.sources.base import InstrumentExporterRegistry

from .common import all_pages_context
from .. import apps
from .. import db
from .. import models
from ..cachers import StaticDataCache
from ..view_models import Exporter, SourceApiActualityInfo


class ActualizeHistoryRedirectView(generic.RedirectView):
    permanent = False
    query_string = False
    pattern_name = apps.FinStorageConfig.name + ':exporters_detail'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.static_data_cache = StaticDataCache()
        self.database_context = db.DatabaseContext()

    def get_exporter(self) -> typing.Optional[Exporter]:
        pk = self.kwargs.get('id')

        # If not defined, it's an error.
        if pk is None:
            raise ValueError(
                f"Actualize view {self.__class__.__name__} must be called with an object id "
                "in the URLconf."
            )

        self.logger.debug(f"Try to get exporter with pk={pk}")
        # noinspection PyUnresolvedReferences
        try:
            exporter = self.database_context.get_exporter_by_id(pk)
        except models.Exporter.DoesNotExist:
            return None

        return exporter

    def get(self, request, *args, **kwargs):
        exporter = self.get_exporter()
        if exporter is None:
            return HttpResponseNotFound()

        if exporter.disabled:
            self.logger.info(f"Can`t actualize history for disabled exporter {exporter.unique_code!r}")
            return super().get(request, *args, **kwargs)

        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        has_history = True
        last_date = max((dt for _, dt in exporter.downloaded_intervals), default=None)
        if last_date is None:
            # there is no history at all
            has_history = False
            is_actual = False
            # if no history then we download only two days: yesterday and today
            last_date = yesterday - datetime.timedelta(days=1)
        else:
            is_actual = last_date >= today

        if is_actual:
            message = f"The history data of exporter {exporter.unique_code!r} is already actual"
            self.logger.info(message)
            messages.info(self.request, message)

        else:
            moment_from = datetime.datetime.combine(
                last_date + datetime.timedelta(days=1),
                datetime.time.min,
                tzinfo=timezone.get_current_timezone())
            moment_to = datetime.datetime.combine(
                today,
                datetime.time.min,
                tzinfo=moment_from.tzinfo)

            self.logger.info(f"Actualize history in {moment_from.date().isoformat()}..{moment_to.date().isoformat()} "
                             f"for exporter {exporter.unique_code!r}")

            downloaded_history_data = tuple(self.static_data_cache.download_history_data(
                exporter, moment_from, moment_to))
            self.database_context.save_history_data(
                exporter.id,
                downloaded_history_data,
                moment_from.date(),
                moment_to.date())
            self.static_data_cache.drop_history_data_from_cache(exporter, moment_from, moment_to)

            if has_history:
                messages.success(
                    self.request,
                    f"The history data of exporter {exporter.unique_code!r} was actualized successfully "
                    f"from {moment_from.isoformat()} till {moment_to.isoformat()}")
            else:
                messages.success(
                    self.request,
                    f"The exporter {exporter.unique_code!r} had no history at all so it was actualized only "
                    f"from {moment_from.isoformat()} till {moment_to.isoformat()}")

        return super().get(request, *args, **kwargs)


class SourceApiActualityForm(forms.Form):
    required_css_class = 'required'
    error_css_class = 'errors'

    source = forms.MultipleChoiceField(widget=widgets.CheckboxSelectMultiple(attrs={'checked': False}),
                                       required=False)

    def __init__(self,
                 available_sources: typing.List[SourceApiActualityInfo],
                 instance=None,
                 data=None, files=None, auto_id='id_%s', prefix=None,
                 initial=None, error_class=ErrorList, label_suffix=None,
                 empty_permitted=False, field_order=None, use_required_attribute=None, renderer=None):
        super().__init__(data, files, auto_id, prefix,
                         initial, error_class, label_suffix,
                         empty_permitted, field_order, use_required_attribute, renderer)

        self.instance = instance

        source_field = self.fields['source']
        source_field.choices = [
            (str(source.id), source)
            for source
            in available_sources
        ]

    @property
    def model_name(self):
        return 'source_actuality'

    def save(self):
        return self.instance


class SourceApiActualityView(generic.edit.CreateView):
    template_name = apps.FinStorageConfig.name + '/sources_actuality.html'
    form_class = SourceApiActualityForm

    available_exporters_registries: typing.OrderedDict[int, InstrumentExporterRegistry]
    title = 'Check sources API actuality'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.static_data_cache = StaticDataCache()
        self.database_context = db.DatabaseContext()

    def get_success_url(self):
        return reverse_lazy(
            apps.FinStorageConfig.name + ':sources_actuality',
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

        self.available_exporters_registries = self.static_data_cache.get_available_exporters_registries()
        exporter_registry_by_type = {
            analyzers.get_full_path(exporter_registry.factory.__class__): (exporter_registry_id, exporter_registry)
            for exporter_registry_id, exporter_registry
            in self.available_exporters_registries.items()}
        db_source_api_actualities: typing.Dict[str, SourceApiActualityInfo] = {
            source_api_actuality.raw_exporter_type: source_api_actuality
            for source_api_actuality
            in self.database_context.get_all_source_api_actualities()}

        available_sources = []
        for exporter_type, (exporter_registry_id, exporter_registry) in exporter_registry_by_type.items():
            db_source_api_actuality = db_source_api_actualities.get(exporter_type, None)
            check_error_message = (None
                                   if db_source_api_actuality is None
                                   else db_source_api_actuality.check_error_message)
            last_check_moment = (None
                                 if db_source_api_actuality is None
                                 else db_source_api_actuality.last_check_moment)

            available_sources.append(SourceApiActualityInfo(
                id=exporter_registry_id,
                raw_exporter_type=None,
                exporter_registry=exporter_registry,
                check_error_message=check_error_message,
                last_check_moment=last_check_moment))

        # add sources from DB but not found in available exporters registries
        for source_api_actuality in db_source_api_actualities.values():
            if source_api_actuality.raw_exporter_type not in exporter_registry_by_type:
                available_sources.append(SourceApiActualityInfo(
                    id=source_api_actuality.id,
                    raw_exporter_type=source_api_actuality.raw_exporter_type,
                    exporter_registry=None,
                    check_error_message=source_api_actuality.check_error_message,
                    last_check_moment=source_api_actuality.last_check_moment))

        kwargs['available_sources'] = available_sources

        return kwargs

    def form_valid(self, form: SourceApiActualityForm):
        cleaned_data: dict[str, typing.Any] = form.cleaned_data

        source_choices = cleaned_data['source']
        if not source_choices:
            form.add_error(
                None,
                "Nothing to check. Select sources."
            )
            return super().form_invalid(form)

        available_exporters_registries = self.static_data_cache.get_available_exporters_registries()
        selected_exporters_factories = [available_exporters_registries[int(source_code)].factory
                                        for source_code
                                        in source_choices]

        was_error = False
        for exporter_factory in selected_exporters_factories:
            # never cache actuality checks:
            checker = exporter_factory.create_api_actuality_checker(UrlDownloader(DummyCacher()))
            exporter_type = analyzers.get_full_path(exporter_factory.__class__)

            self.logger.info(f"Check source API actuality for {exporter_factory}")
            # noinspection PyBroadException
            try:
                checker.check()

            except Exception:
                _, exc_value, _ = sys.exc_info()
                error_message = str(exc_value)
                was_error = True
                self.logger.info(f"Source API for {exporter_factory} is not actual: {error_message}")

            else:
                error_message = None
                self.logger.info(f"Source API for {exporter_factory} is actual")

            self.database_context.update_source_api_actuality(exporter_type, error_message, timezone.now())

        if was_error:
            messages.error(self.request, "There was error(s) while checking API actuality")
        else:
            messages.success(self.request, "All API actuality checks succeeded")

        return super().form_valid(form)
