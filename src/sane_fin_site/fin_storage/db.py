import datetime
import logging
import typing

from django.db import transaction, models as django_models
from sane_finances.communication.downloader import DownloadError
from sane_finances.inspection import analyzers, serialize
from sane_finances.sources.base import (
    InstrumentExporterRegistry, InstrumentValue, InstrumentExporterFactory, DownloadParameterValuesStorage)
from sane_finances.sources.generic import get_instrument_exporter_by_factory

from . import models
from .cachers import StaticDataCache
from .view_models import SourceApiActualityInfo, Exporter

T = typing.TypeVar('T')


class DatabaseContext:

    def __init__(self):
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

    def get_all_source_api_actualities(self) -> typing.Iterable[SourceApiActualityInfo]:
        self.logger.info("Read all source API actualities")

        # noinspection PyUnresolvedReferences
        queryset: django_models.QuerySet = models.SourceApiActuality.objects
        source_api_actuality: models.SourceApiActuality
        return tuple(
            SourceApiActualityInfo(
                id=-source_api_actuality.pk,  # negate to differentiate from external source id
                raw_exporter_type=source_api_actuality.exporter_type,
                exporter_registry=None,
                check_error_message=source_api_actuality.check_error_message,
                last_check_moment=(None
                                   if source_api_actuality.last_check_moment is None
                                   else source_api_actuality.last_check_moment))
            for source_api_actuality
            in queryset.all())

    # noinspection PyUnresolvedReferences
    def update_source_api_actuality(
            self,
            exporter_type: str,
            check_error_message: str,
            check_moment: datetime.datetime):
        self.logger.info(f"Update source API actuality for {exporter_type!r} "
                         f"on {check_moment.isoformat()} "
                         f"with error message {check_error_message!r}")

        queryset: django_models.QuerySet = models.SourceApiActuality.objects

        attrs_to_update = {'check_error_message': check_error_message, 'last_check_moment': check_moment}

        try:
            source_api_actuality: models.SourceApiActuality = queryset.get(exporter_type=exporter_type)

        except models.SourceApiActuality.DoesNotExist:
            attrs_to_update['exporter_type'] = exporter_type
            source_api_actuality = queryset.create(**attrs_to_update)

        else:
            for attr_name, attr_value in attrs_to_update.items():
                setattr(source_api_actuality, attr_name, attr_value)

            source_api_actuality.save()

        self.logger.info(f"Source API actuality for {exporter_type!r} updated")

        return source_api_actuality.pk

    # noinspection PyMethodMayBeStatic
    def _build_instance(
            self,
            json_string: str,
            root_data_class: typing.Type[T],
            download_param_values_storage: DownloadParameterValuesStorage,
            root_factory: typing.Callable[..., typing.Any]):
        if not json_string:
            return None

        instance_analyzer = analyzers.FlattenedAnnotatedInstanceAnalyzer(
            root_data_class,
            download_param_values_storage)
        instance_builder = analyzers.InstanceBuilder(
            root_factory,
            download_param_values_storage)
        instance_factory_converter = analyzers.InstanceFactoryDataConverter(instance_analyzer)
        serializer = serialize.FlattenedDataJsonSerializer(instance_analyzer, download_param_values_storage)

        flattened_data = serializer.deserialize_flattened_data(json_string, decode_dynamic_enums=True)
        factory_data = instance_factory_converter.get_instance_factory_data(flattened_data)
        instance = instance_builder.build_instance(factory_data)
        return instance

    # noinspection PyMethodMayBeStatic
    def _queryset(self) -> django_models.QuerySet:
        # noinspection PyUnresolvedReferences
        return models.Exporter.objects

    # noinspection PyMethodMayBeStatic
    def _get_exporter_registry(
            self,
            exporter_type: str,
            error_messages: typing.List[str]) -> typing.Optional[InstrumentExporterRegistry]:
        factory_class = analyzers.get_by_full_path(exporter_type)
        if factory_class is None:
            error_messages.append(f"Not found exporter factory by path {exporter_type!r}")
            return None
        exporter_registry = get_instrument_exporter_by_factory(factory_class)
        if exporter_registry is None:
            error_messages.append(f"Instrument exporter with factory {factory_class} not found or not registered")

        return exporter_registry

    def _create_exporter(
            self,
            exporter_model: models.Exporter,
            with_history_data: bool = True,
            with_download_parameters: bool = True) -> Exporter:
        error_messages = []
        download_info_parameters = None
        download_history_parameters = None
        history_data = {}
        downloaded_intervals = []

        raw_exporter_type = exporter_model.exporter_type
        exporter_registry = self._get_exporter_registry(exporter_model.exporter_type, error_messages)
        if exporter_registry is not None and with_download_parameters:
            try:
                download_param_values_storage = \
                    StaticDataCache().download_parameter_values_storage(exporter_registry.factory)

                download_info_parameters = self._build_instance(
                    exporter_model.download_info_parameters,
                    exporter_registry.factory.download_parameters_factory.download_info_parameters_class,
                    download_param_values_storage,
                    exporter_registry.factory.download_parameters_factory.download_info_parameters_factory)

                download_history_parameters = self._build_instance(
                    exporter_model.download_history_parameters,
                    exporter_registry.factory.download_parameters_factory.download_history_parameters_class,
                    download_param_values_storage,
                    exporter_registry.factory.download_parameters_factory.download_history_parameters_factory)

            except DownloadError as ex:
                error_messages.append(f"Download parameters error: {ex}")

        if with_history_data:
            # noinspection PyUnresolvedReferences
            for instrument_value in exporter_model.history_data.all():
                instrument_value: models.InstrumentValue
                history_data[instrument_value.moment] = InstrumentValue(
                    moment=instrument_value.moment,
                    value=instrument_value.value)

        # noinspection PyUnresolvedReferences
        for downloaded_interval in exporter_model.downloaded_intervals.all():
            downloaded_interval: models.DownloadedInterval
            downloaded_intervals.append((downloaded_interval.date_from, downloaded_interval.date_to))

        error_message = '\n'.join(error_messages) if error_messages else None
        has_gaps = len(downloaded_intervals) > 1
        last_downloaded_date = max((dt for _, dt in downloaded_intervals), default=datetime.date.min)
        today = datetime.date.today()
        if today.isoweekday() == 1:  # monday
            last_working_day = today - datetime.timedelta(days=3)  # friday
        elif today.isoweekday() == 7:  # sunday
            last_working_day = today - datetime.timedelta(days=2)  # friday
        else:
            last_working_day = today - datetime.timedelta(days=1)
        is_actual = last_downloaded_date >= last_working_day

        return Exporter(
            id=exporter_model.pk,
            unique_code=exporter_model.unique_code,
            description=exporter_model.description,
            is_active=exporter_model.is_active,
            exporter_registry=exporter_registry,
            download_info_parameters=download_info_parameters,
            download_info_parameters_str=exporter_model.download_info_parameters,
            download_history_parameters=download_history_parameters,
            download_history_parameters_str=exporter_model.download_history_parameters,
            history_data=history_data,
            downloaded_intervals=downloaded_intervals,
            raw_exporter_type=raw_exporter_type,
            error_message=error_message,
            has_gaps=has_gaps,
            is_actual=is_actual)

    def get_all_exporters(self) -> typing.Iterable[Exporter]:
        return tuple(self._create_exporter(exporter_model, with_history_data=False, with_download_parameters=False)
                     for exporter_model
                     in self._queryset().all()
                     .prefetch_related('downloaded_intervals'))

    def is_exporter_code_unique(self, exporter_code: str, pk: typing.Optional):
        if pk is None:
            return not (self._queryset()
                        .filter(unique_code=exporter_code)
                        .exists())

        return not (self._queryset()
                    .filter(unique_code=exporter_code)
                    .exclude(pk=pk)
                    .exists())

    def get_all_exporters_as_model(self) -> typing.Iterable[models.Exporter]:
        return self._queryset().all()

    def get_exporters_as_model(self, exporters: typing.Iterable[Exporter]) -> typing.Iterable[models.Exporter]:
        return (self._queryset().all()
                .prefetch_related('history_data')
                .prefetch_related('downloaded_intervals')
                .filter(pk__in=[exporter.id for exporter in exporters]))

    def get_exporter_by_id(self, pk) -> Exporter:
        return self._create_exporter(
            self._queryset()
                .prefetch_related('history_data')
                .prefetch_related('downloaded_intervals')
                .get(pk=pk))

    def get_exporter_by_code(self, unique_code: str) -> Exporter:
        return self._create_exporter(
            self._queryset()
                .prefetch_related('history_data')
                .prefetch_related('downloaded_intervals')
                .get(unique_code=unique_code))

    # noinspection PyMethodMayBeStatic
    def _serialize_attr_values(
            self,
            factory: InstrumentExporterFactory,
            flatten_download_parameters: bool,
            **attr_values):
        new_attr_values = {}
        for attr_name, new_attr_value in attr_values.items():
            if flatten_download_parameters:
                root_data_classes = {
                    'download_info_parameters': factory.download_parameters_factory.download_info_parameters_class,
                    'download_history_parameters': factory.download_parameters_factory.download_history_parameters_class
                }
                if attr_name in root_data_classes:
                    download_param_values_storage = StaticDataCache().download_parameter_values_storage(factory)
                    instance_analyzer = analyzers.FlattenedAnnotatedInstanceAnalyzer(
                        root_data_classes[attr_name],
                        download_param_values_storage)
                    instance_flattener = analyzers.InstanceFlattener(instance_analyzer, download_param_values_storage)
                    serializer = serialize.FlattenedDataJsonSerializer(instance_analyzer, download_param_values_storage)

                    flattened_data = instance_flattener.get_flattened_data_from(new_attr_value)
                    new_attr_value = serializer.serialize_flattened_data(flattened_data)

            new_attr_values[attr_name] = new_attr_value

        return new_attr_values

    def update_exporter(self, pk, flatten_download_parameters: bool, **update_attrs) -> None:
        self.logger.info(f"Update exporter with pk={pk} with attribute values {update_attrs}")

        exporter: models.Exporter = self._queryset().get(pk=pk)
        error_messages = []
        exporter_registry = self._get_exporter_registry(
            update_attrs.get('exporter_type', exporter.exporter_type),
            error_messages)

        if exporter_registry is not None:
            update_attrs = self._serialize_attr_values(
                exporter_registry.factory,
                flatten_download_parameters,
                **update_attrs)

            for attr_name, new_attr_value in update_attrs.items():
                if not hasattr(exporter, attr_name):
                    raise ValueError(f"Exporter has no attribute with name {attr_name!r}")

                setattr(exporter, attr_name, new_attr_value)

        if error_messages:
            error_message = '\n'.join(error_messages)
            raise ValueError(f"Update exporter errors: {error_message}")

        exporter.save()

    def create_exporter(
            self,
            exporter_registry: InstrumentExporterRegistry,
            flatten_download_parameters: bool,
            **create_attrs) -> typing.Any:
        self.logger.info(f"Create exporter from registry {exporter_registry} with attributes {create_attrs}")

        create_attrs = self._serialize_attr_values(
            exporter_registry.factory,
            flatten_download_parameters,
            **create_attrs)

        exporter_type = analyzers.get_full_path(exporter_registry.factory.__class__)
        create_attrs['exporter_type'] = exporter_type

        exporter = self._queryset().create(**create_attrs)

        return exporter.id

    def update_or_create(self, exporter: Exporter) -> bool:
        self.logger.info(f"Update or create exporter {exporter.unique_code!r}")

        error_messages = []
        exporter_registry = self._get_exporter_registry(exporter.raw_exporter_type, error_messages)

        if error_messages:
            error_message = '\n'.join(error_messages)
            raise ValueError(f"Update exporter errors: {error_message}")

        attr_values = {
            'unique_code': exporter.unique_code,
            'description': exporter.description,
            'is_active': exporter.is_active,
            'exporter_type': exporter.raw_exporter_type,
            'download_info_parameters': exporter.download_info_parameters_str,
            'download_history_parameters': exporter.download_history_parameters_str
        }

        exporter_model: typing.Optional[models.Exporter] = None
        # noinspection PyUnresolvedReferences
        try:
            exporter_model = self._queryset().get(unique_code=exporter.unique_code)
        except models.Exporter.DoesNotExist:
            pass

        if exporter_model is None:
            _ = self.create_exporter(exporter_registry, False, **attr_values)
        else:
            self.update_exporter(exporter_model.pk, False, **attr_values)

        return exporter_model is None

    def delete_exporter(self, pk):
        self.logger.info(f"Delete exporter with pk={pk}")
        self._queryset().get(pk=pk).delete()

    def update_or_create_history_data(
            self,
            exporter: models.Exporter,
            history_data: typing.Collection[InstrumentValue]):
        self.logger.info(f"Update or save {len(history_data)} history data items "
                         f"for exporter {exporter.unique_code!r}")
        for history_item in history_data:
            _ = models.InstrumentValue.objects.update_or_create(
                exporter=exporter, moment=history_item.moment,
                defaults={'value': history_item.value})

    def update_or_create_downloaded_intervals(
            self,
            exporter: models.Exporter,
            downloaded_intervals: typing.Collection[typing.Tuple[datetime.date, datetime.date]]):
        self.logger.info(f"Update or save {len(downloaded_intervals)} downloaded intervals "
                         f"for exporter {exporter.unique_code!r}")
        for date_from, date_to in downloaded_intervals:
            _ = models.DownloadedInterval.objects.update_or_create(
                exporter=exporter,
                date_from=date_from,
                date_to=date_to)

    def save_history_data(
            self,
            pk,
            history_data: typing.Collection[InstrumentValue],
            date_from: datetime.date,
            date_to: datetime.date):
        self.logger.info(f"Save {len(history_data)} history data items "
                         f"for exporter with pk={pk} "
                         f"inside {date_from.isoformat()}..{date_to.isoformat()}")

        with transaction.atomic(savepoint=False):
            exporter: models.Exporter = (self._queryset()
                                         .prefetch_related('downloaded_intervals')
                                         .get(pk=pk))

            min_history_date = (min(history_data, key=lambda it: it.moment.date()).moment.date()
                                if history_data
                                else None)
            max_history_date = (max(history_data, key=lambda it: it.moment.date()).moment.date()
                                if history_data
                                else None)
            assert ((history_data and min_history_date is not None and max_history_date is not None) or
                    (not history_data and min_history_date is None and max_history_date is None))

            if min_history_date is not None and min_history_date < date_from:
                date_from = min_history_date
            if max_history_date is not None and max_history_date > date_to:
                date_to = max_history_date

            date_before_from = date_from - datetime.timedelta(days=1)
            date_after_to = date_to + datetime.timedelta(days=1)

            # noinspection PyUnresolvedReferences
            all_downloaded_intervals = exporter.downloaded_intervals.all()

            affected_intervals: typing.List[models.DownloadedInterval] = [
                downloaded_interval
                for downloaded_interval
                in all_downloaded_intervals
                if downloaded_interval.date_to >= date_before_from and downloaded_interval.date_from <= date_after_to]

            if affected_intervals:
                # check every edge for penetrating into any previously downloaded interval
                pierce_left = bool([
                    downloaded_interval
                    for downloaded_interval
                    in all_downloaded_intervals
                    if downloaded_interval.date_from <= date_before_from <= downloaded_interval.date_to])
                pierce_right = bool([
                    downloaded_interval
                    for downloaded_interval
                    in all_downloaded_intervals
                    if downloaded_interval.date_from <= date_after_to <= downloaded_interval.date_to])

                # if history is empty and new downloaded interval stick out by any edge, then nothing to do
                if history_data or (pierce_left and pierce_right):
                    # if new downloaded interval penetrate into previously downloaded intervals by both edges,
                    # then save it, even if downloaded history is empty
                    # (e.g. to join several previously downloaded intervals)
                    min_date_from = min(date_from, min(affected_intervals, key=lambda it: it.date_from).date_from)
                    max_date_to = max(date_to, max(affected_intervals, key=lambda it: it.date_to).date_to)

                    # if edge is stick out then adjust it to history data dates
                    # (to prevent creating interval with empty data inside it)
                    if not pierce_left and min_history_date > date_from:
                        min_date_from = min_history_date
                    if not pierce_right and max_history_date < date_to:
                        max_date_to = max_history_date

                    interval_to_update = min(affected_intervals, key=lambda it: it.id)

                    for interval_to_delete in affected_intervals:
                        if interval_to_delete.pk != interval_to_update.pk:
                            interval_to_delete.delete()

                    interval_to_update.date_from = min_date_from
                    interval_to_update.date_to = max_date_to
                    interval_to_update.save()

            else:
                if history_data:
                    # create new downloaded interval only if we have any history data
                    # noinspection PyUnresolvedReferences
                    models.DownloadedInterval.objects.create(
                        exporter=exporter,
                        date_from=min_history_date,
                        date_to=max_history_date)

            self.update_or_create_history_data(exporter, history_data)

        self.logger.info(f"History data for exporter with pk={pk} "
                         f"inside {date_from.isoformat()}..{date_to.isoformat()} saved successfully")
