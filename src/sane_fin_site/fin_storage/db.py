import datetime
import typing

from django.db import transaction, models as django_models
from sane_finances.inspection import analyzers, serialize
from sane_finances.sources.base import (
    InstrumentExporterRegistry, InstrumentValue, InstrumentExporterFactory, DownloadParameterValuesStorage)
from sane_finances.sources.generic import get_instrument_exporter_by_factory

from . import models
from .cachers import StaticDataCache
from .view_models import SourceApiActualityInfo, Exporter

T = typing.TypeVar('T')


class DatabaseContext:

    @classmethod
    def get_all_source_api_actualities(cls) -> typing.Iterable[SourceApiActualityInfo]:
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
    @classmethod
    def update_source_api_actuality(
            cls,
            exporter_type: str,
            check_error_message: str,
            check_moment: datetime.datetime):
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

        return source_api_actuality.pk

    @classmethod
    def _build_instance(
            cls,
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

    @classmethod
    def _queryset(cls) -> django_models.QuerySet:
        # noinspection PyUnresolvedReferences
        return models.Exporter.objects

    @classmethod
    def _get_exporter_registry(
            cls,
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

    @classmethod
    def _create_exporter(
            cls,
            exporter_model: models.Exporter,
            with_history_data: bool = True,
            with_download_parameters: bool = True) -> Exporter:
        error_messages = []
        download_info_parameters = None
        download_history_parameters = None
        history_data = {}
        downloaded_intervals = []

        raw_exporter_type = exporter_model.exporter_type
        exporter_registry = cls._get_exporter_registry(exporter_model.exporter_type, error_messages)
        if exporter_registry is not None and with_download_parameters:
            download_param_values_storage = StaticDataCache.download_parameter_values_storage(exporter_registry.factory)
            download_info_parameters = cls._build_instance(
                exporter_model.download_info_parameters,
                exporter_registry.factory.download_parameters_factory.download_info_parameters_class,
                download_param_values_storage,
                exporter_registry.factory.download_parameters_factory.download_info_parameters_factory)
            download_history_parameters = cls._build_instance(
                exporter_model.download_history_parameters,
                exporter_registry.factory.download_parameters_factory.download_history_parameters_class,
                download_param_values_storage,
                exporter_registry.factory.download_parameters_factory.download_history_parameters_factory)

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

    @classmethod
    def get_all_exporters(cls) -> typing.Iterable[Exporter]:
        return tuple(cls._create_exporter(exporter_model, with_history_data=False, with_download_parameters=False)
                     for exporter_model
                     in cls._queryset().all()
                     .prefetch_related('downloaded_intervals'))

    @classmethod
    def is_exporter_code_unique(cls, exporter_code: str, pk: typing.Optional):
        if pk is None:
            return not (cls._queryset()
                        .filter(unique_code=exporter_code)
                        .exists())

        return not (cls._queryset()
                    .filter(unique_code=exporter_code)
                    .exclude(pk=pk)
                    .exists())

    @classmethod
    def get_all_exporters_as_model(cls) -> typing.Iterable[models.Exporter]:
        return cls._queryset().all()

    @classmethod
    def get_exporters_as_model(cls, exporters: typing.Iterable[Exporter]) -> typing.Iterable[models.Exporter]:
        return (cls._queryset().all()
                .prefetch_related('history_data')
                .prefetch_related('downloaded_intervals')
                .filter(pk__in=[exporter.id for exporter in exporters]))

    @classmethod
    def get_exporter_by_id(cls, pk) -> Exporter:
        return cls._create_exporter(cls._queryset()
                                    .prefetch_related('history_data')
                                    .prefetch_related('downloaded_intervals')
                                    .get(pk=pk))

    @classmethod
    def get_exporter_by_code(cls, unique_code: str) -> Exporter:
        return cls._create_exporter(cls._queryset()
                                    .prefetch_related('history_data')
                                    .prefetch_related('downloaded_intervals')
                                    .get(unique_code=unique_code))

    @classmethod
    def _serialize_attr_values(
            cls,
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
                    download_param_values_storage = StaticDataCache.download_parameter_values_storage(factory)
                    instance_analyzer = analyzers.FlattenedAnnotatedInstanceAnalyzer(
                        root_data_classes[attr_name],
                        download_param_values_storage)
                    instance_flattener = analyzers.InstanceFlattener(instance_analyzer, download_param_values_storage)
                    serializer = serialize.FlattenedDataJsonSerializer(instance_analyzer, download_param_values_storage)

                    flattened_data = instance_flattener.get_flattened_data_from(new_attr_value)
                    new_attr_value = serializer.serialize_flattened_data(flattened_data)

            new_attr_values[attr_name] = new_attr_value

        return new_attr_values

    @classmethod
    def update_exporter(cls, pk, flatten_download_parameters: bool, **update_attrs) -> None:
        exporter: models.Exporter = cls._queryset().get(pk=pk)
        error_messages = []
        exporter_registry = cls._get_exporter_registry(
            update_attrs.get('exporter_type', exporter.exporter_type),
            error_messages)

        if exporter_registry is not None:
            update_attrs = cls._serialize_attr_values(
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

    @classmethod
    def create_exporter(
            cls,
            exporter_registry: InstrumentExporterRegistry,
            flatten_download_parameters: bool,
            **create_attrs) -> typing.Any:
        create_attrs = cls._serialize_attr_values(
            exporter_registry.factory,
            flatten_download_parameters,
            **create_attrs)

        exporter_type = analyzers.get_full_path(exporter_registry.factory.__class__)
        create_attrs['exporter_type'] = exporter_type

        exporter = cls._queryset().create(**create_attrs)

        return exporter.id

    @classmethod
    def update_or_create(cls, exporter: Exporter) -> bool:
        error_messages = []
        exporter_registry = cls._get_exporter_registry(exporter.raw_exporter_type, error_messages)

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
            exporter_model = cls._queryset().get(unique_code=exporter.unique_code)
        except models.Exporter.DoesNotExist:
            pass

        if exporter_model is None:
            _ = cls.create_exporter(exporter_registry, False, **attr_values)
        else:
            cls.update_exporter(exporter_model.pk, False, **attr_values)

        return exporter_model is None

    @classmethod
    def delete_exporter(cls, pk):
        cls._queryset().get(pk=pk).delete()

    @classmethod
    def update_or_create_history_data(
            cls,
            exporter: models.Exporter,
            history_data: typing.Iterable[InstrumentValue]):
        for history_item in history_data:
            _ = models.InstrumentValue.objects.update_or_create(
                exporter=exporter, moment=history_item.moment,
                defaults={'value': history_item.value})

    @classmethod
    def update_or_create_downloaded_intervals(
            cls,
            exporter: models.Exporter,
            downloaded_intervals: typing.Iterable[typing.Tuple[datetime.date, datetime.date]]):
        for date_from, date_to in downloaded_intervals:
            _ = models.DownloadedInterval.objects.update_or_create(
                exporter=exporter,
                date_from=date_from,
                date_to=date_to)

    @classmethod
    def save_history_data(
            cls,
            pk,
            history_data: typing.Collection[InstrumentValue],
            date_from: datetime.date,
            date_to: datetime.date):
        exporter: models.Exporter = (cls._queryset()
                                     .prefetch_related('downloaded_intervals')
                                     .get(pk=pk))

        with transaction.atomic(savepoint=False):

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

            cls.update_or_create_history_data(exporter, history_data)
