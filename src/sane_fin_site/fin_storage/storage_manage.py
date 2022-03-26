import abc
import collections
import datetime
import decimal
import json
import logging
import pathlib
import typing

from django.core import serializers
from django.db import transaction
from sane_finances.sources.base import InstrumentValue

from . import models
from . import view_models
from . import db


class ImportSettingsItem(abc.ABC):
    """ View model for import settings item
    """
    exporter_unique_code: str
    is_new: bool  # there is no exporter in database with such unique code
    history_data: typing.Collection
    downloaded_intervals: typing.Collection

    @property
    @abc.abstractmethod
    def has_exporter_info(self) -> bool:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def description(self) -> str:
        raise NotImplementedError


class DjangoImportSettingsItem(ImportSettingsItem):
    history_data: typing.Tuple[serializers.base.DeserializedObject, ...]
    downloaded_intervals: typing.Tuple[serializers.base.DeserializedObject, ...]
    exporter_model: typing.Optional[serializers.base.DeserializedObject]

    def __init__(
            self,
            exporter_unique_code: str,
            exporter_model: typing.Optional[serializers.base.DeserializedObject],
            is_new: bool,
            history_data: typing.Tuple[serializers.base.DeserializedObject, ...],
            downloaded_intervals: typing.Tuple[serializers.base.DeserializedObject, ...]):
        if any(not isinstance(history_item.object, models.InstrumentValue)
               for history_item
               in history_data):
            raise TypeError("History data contains not instrument value item")
        if any(not isinstance(downloaded_interval_item.object, models.DownloadedInterval)
               for downloaded_interval_item
               in downloaded_intervals):
            raise TypeError("Downloaded intervals data contains not downloaded interval item")

        self.exporter_unique_code = exporter_unique_code
        self.exporter_model = exporter_model
        self.is_new = is_new

        self.history_data = history_data
        self.downloaded_intervals = downloaded_intervals

    @property
    def has_exporter_info(self) -> bool:
        return self.exporter_model is not None

    @property
    def description(self) -> str:
        return str(self.exporter_model.object)


class JsonImportSettingsItem(ImportSettingsItem):
    history_data: typing.Tuple[InstrumentValue, ...]
    downloaded_intervals: typing.Tuple[typing.Tuple[datetime.date, datetime.date], ...]
    exporter: typing.Optional[view_models.Exporter]

    def __init__(
            self,
            exporter_unique_code: str,
            exporter: typing.Optional[view_models.Exporter],
            is_new: bool,
            history_data: typing.Tuple[InstrumentValue, ...],
            downloaded_intervals: typing.Tuple[typing.Tuple[datetime.date, datetime.date], ...]):
        if any(not isinstance(history_item, InstrumentValue)
               for history_item
               in history_data):
            raise TypeError("History data contains not instrument value item")

        self.exporter_unique_code = exporter_unique_code
        self.exporter = exporter
        self.is_new = is_new

        self.history_data = history_data
        self.downloaded_intervals = downloaded_intervals

    @property
    def has_exporter_info(self) -> bool:
        return self.exporter is not None

    @property
    def description(self) -> str:
        return str(self.exporter)


class ImportSettingsData(abc.ABC):
    """ View model for import settings view
    """
    file_name: str
    file_content: str
    items: typing.Tuple[ImportSettingsItem, ...] = ()


class DjangoImportSettingsData(ImportSettingsData):
    def __init__(self, file_name: str, file_content: str, items: typing.Tuple[DjangoImportSettingsItem, ...]):
        self.file_name = file_name
        self.file_content = file_content
        self.items = items


class JsonImportSettingsData(ImportSettingsData):
    def __init__(self, file_name: str, file_content: str, items: typing.Tuple[JsonImportSettingsItem, ...]):
        self.file_name = file_name
        self.file_content = file_content
        self.items = items


class SpecificSettingsManager(abc.ABC):
    @abc.abstractmethod
    def serialize_settings(
            self,
            file_name: str,
            exporters: typing.Collection[view_models.Exporter],
            target_stream) -> typing.Optional[str]:
        raise NotImplementedError

    @abc.abstractmethod
    def deserialize_settings(
            self,
            settings_file_name: str,
            settings_file_content: str) -> ImportSettingsData:
        raise NotImplementedError

    @abc.abstractmethod
    def save_settings_data(
            self,
            settings_data: ImportSettingsData,
            exporters_codes: typing.Iterable[str],
            history_data_codes: typing.Iterable[str],
            downloaded_intervals_codes: typing.Iterable[str]):
        raise NotImplementedError


class DjangoXmlSettingsManager(SpecificSettingsManager):

    def __init__(self):
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)
        self.database_context = db.DatabaseContext()

    def serialize_settings(
            self,
            file_name: str,
            exporters: typing.Collection[view_models.Exporter],
            target_stream) -> typing.Optional[str]:
        self.logger.info(f"Serialize settings for {len(exporters)} exporters into {file_name}")

        exporters_as_model = self.database_context.get_exporters_as_model(exporters)
        data_to_export = []

        for exporter_as_model in exporters_as_model:
            data_to_export.append(exporter_as_model)
            # noinspection PyUnresolvedReferences
            data_to_export.extend(exporter_as_model.history_data.all())
            # noinspection PyUnresolvedReferences
            data_to_export.extend(exporter_as_model.downloaded_intervals.all())

        _ = serializers.serialize(
            'xml',
            data_to_export,
            stream=target_stream,
            use_natural_foreign_keys=True,
            use_natural_primary_keys=True)

        self.logger.info("Serialization finished")

        return None

    def deserialize_settings(
            self,
            settings_file_name: str,
            settings_file_content: str) -> DjangoImportSettingsData:
        self.logger.info(f"Deserialize settings from {settings_file_name}")

        settings_items: collections.OrderedDict[str, DjangoImportSettingsItem] = collections.OrderedDict()

        if settings_file_content:
            self.logger.debug("Deserialize Django XML")
            deserialized_objects: typing.List[serializers.base.DeserializedObject]
            deserialized_objects = list(serializers.deserialize(
                "xml",
                settings_file_content,
                handle_forward_references=True))
            self.logger.debug(f"Deserialized {len(deserialized_objects)} objects from Django XML")

            exporters: collections.OrderedDict[str, serializers.base.DeserializedObject] = collections.OrderedDict()
            instrument_values: typing.Dict[str, typing.List[serializers.base.DeserializedObject]] = {}
            downloaded_intervals: typing.Dict[str, typing.List[serializers.base.DeserializedObject]] = {}

            for deserialized_object in deserialized_objects:
                if isinstance(deserialized_object.object, models.Exporter):
                    exporters[deserialized_object.object.unique_code] = deserialized_object

                elif isinstance(deserialized_object.object, (models.InstrumentValue, models.DownloadedInterval)):
                    model, dst_dict = (
                        (models.InstrumentValue, instrument_values)
                        if isinstance(deserialized_object.object, models.InstrumentValue)
                        else (models.DownloadedInterval, downloaded_intervals))

                    exporter: models.Exporter = getattr(deserialized_object.object, 'exporter', None)
                    if exporter is None:
                        exporter_unique_code = deserialized_object.deferred_fields[model.exporter.field][0]
                    else:
                        exporter_unique_code = exporter.unique_code

                    if exporter_unique_code not in dst_dict:
                        dst_dict[exporter_unique_code] = []
                    dst_dict[exporter_unique_code].append(deserialized_object)

            # at first read all explicit exporters (which exist in DB or in XML)
            for deserialized_exporter in exporters.values():
                exporter = deserialized_exporter.object
                settings_item = DjangoImportSettingsItem(
                    exporter_unique_code=exporter.unique_code,
                    exporter_model=deserialized_exporter,
                    is_new=exporter.pk is None,
                    history_data=tuple(instrument_values.get(exporter.unique_code, [])),
                    downloaded_intervals=tuple(downloaded_intervals.get(exporter.unique_code, []))
                )

                settings_items[exporter.unique_code] = settings_item

                if exporter.unique_code in instrument_values:
                    del instrument_values[exporter.unique_code]

                if exporter.unique_code in downloaded_intervals:
                    del downloaded_intervals[exporter.unique_code]

            # read all unknown exporters (not found in DB or in XML but referenced from relative objects)
            for exporter_unique_code in set(instrument_values.keys() | downloaded_intervals.keys()):
                settings_item = DjangoImportSettingsItem(
                    exporter_unique_code=exporter_unique_code,
                    exporter_model=None,
                    is_new=True,
                    history_data=tuple(instrument_values.get(exporter_unique_code, [])),
                    downloaded_intervals=tuple(downloaded_intervals.get(exporter_unique_code, []))
                )

                settings_items[exporter_unique_code] = settings_item

        settings_data = DjangoImportSettingsData(
            file_name=settings_file_name,
            file_content=settings_file_content,
            items=tuple(settings_items.values())
        )
        self.logger.info(f"Settings deserialized from {settings_file_name}")

        return settings_data

    def save_settings_data(
            self,
            settings_data: DjangoImportSettingsData,
            exporters_codes: typing.Iterable[str],
            history_data_codes: typing.Iterable[str],
            downloaded_intervals_codes: typing.Iterable[str]):
        assert isinstance(settings_data, DjangoImportSettingsData)
        self.logger.info("Save settings data")

        settings_items_by_exporter_code: typing.Dict[str, DjangoImportSettingsItem] = {
            settings_item.exporter_unique_code: settings_item
            for settings_item
            in settings_data.items}

        with transaction.atomic(savepoint=False):
            for selected_exporter_code in exporters_codes:
                deserialized_exporter = settings_items_by_exporter_code[selected_exporter_code].exporter_model
                self.logger.debug(f"Save deserialized exporter with code {selected_exporter_code}")
                deserialized_exporter.save()

            all_db_exporters = {exporter_model.unique_code: exporter_model
                                for exporter_model
                                in self.database_context.get_all_exporters_as_model()}

            # save history data
            for selected_exporter_code in history_data_codes:
                self.logger.debug(f"Save deserialized instrument values "
                                  f"for exporter with code {selected_exporter_code}")
                for deserialized_instrument_value in \
                        settings_items_by_exporter_code[selected_exporter_code].history_data:
                    if getattr(deserialized_instrument_value.object, 'exporter', None) is None:
                        deserialized_instrument_value.object.exporter = all_db_exporters[selected_exporter_code]
                    deserialized_instrument_value.save()

            # save downloaded intervals
            for selected_exporter_code in downloaded_intervals_codes:
                self.logger.debug(f"Save downloaded intervals "
                                  f"for exporter with code {selected_exporter_code}")
                for deserialized_downloaded_interval in \
                        settings_items_by_exporter_code[selected_exporter_code].downloaded_intervals:
                    if getattr(deserialized_downloaded_interval.object, 'exporter', None) is None:
                        deserialized_downloaded_interval.object.exporter = all_db_exporters[selected_exporter_code]
                    deserialized_downloaded_interval.save()

        self.logger.info("Settings data saved")


class JsonSettingsManager(SpecificSettingsManager):

    def __init__(self):
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)
        self.database_context = db.DatabaseContext()

    # noinspection PyMethodMayBeStatic
    def as_dict(self, exporter: view_models.Exporter):
        return {
            'unique_code': exporter.unique_code,
            'description': exporter.description,
            'is_active': exporter.is_active,
            'exporter_type': exporter.exporter_type,
            'download_info_parameters': exporter.download_info_parameters_str,
            'download_history_parameters': exporter.download_history_parameters_str,
            'history_data': [
                {
                    'moment': history_item.moment.isoformat(),
                    'value': float(history_item.value)
                }
                for history_item in
                exporter.history_data.values()
            ],
            'downloaded_intervals': [
                {
                    'date_from': date_from.isoformat(),
                    'date_to': date_to.isoformat()
                }
                for date_from, date_to in
                exporter.downloaded_intervals
            ]
        }

    def serialize_settings(
            self,
            file_name: str,
            exporters: typing.Collection[view_models.Exporter],
            target_stream) -> typing.Optional[str]:
        self.logger.info(f"Serialize settings for {len(exporters)} exporters into {file_name}")

        exporters_with_full_data = [
            self.database_context.get_exporter_by_code(exporter.unique_code)
            for exporter in
            exporters]

        exporters_as_dict = {
            'version': '1',
            'exporters': [
                self.as_dict(exporter)
                for exporter
                in exporters_with_full_data
            ]
        }
        exporters_as_str = json.dumps(exporters_as_dict)

        self.logger.info("Serialization finished")
        return exporters_as_str

    # noinspection PyMethodMayBeStatic
    def _get_string_field_value(self, json_data: typing.Dict[str, str], field_name: str):
        raw_value = json_data[field_name]
        if raw_value is None:
            return raw_value
        return str(raw_value)

    def deserialize_settings(
            self,
            settings_file_name: str,
            settings_file_content: str) -> ImportSettingsData:
        self.logger.info(f"Deserialize settings from {settings_file_name}")

        settings_items: collections.OrderedDict[str, JsonImportSettingsItem] = collections.OrderedDict()

        if settings_file_content:
            self.logger.debug(f"Parse JSON from {settings_file_name}")
            json_data = json.loads(settings_file_content)

            # version = json_data['version']
            exporters_data = json_data['exporters']
            self.logger.debug(f"Parsed {len(exporters_data)} exporters")
            for exporter_data in exporters_data:
                # noinspection PyTypeChecker
                exporter = view_models.Exporter(
                    id=None,
                    unique_code=self._get_string_field_value(exporter_data, 'unique_code'),
                    description=self._get_string_field_value(exporter_data, 'description'),
                    is_active=bool(exporter_data['is_active']),
                    exporter_registry=None,
                    raw_exporter_type=self._get_string_field_value(exporter_data, 'exporter_type'),
                    download_info_parameters=None,
                    download_info_parameters_str=self._get_string_field_value(
                        exporter_data,
                        'download_info_parameters'),
                    download_history_parameters=None,
                    download_history_parameters_str=self._get_string_field_value(
                        exporter_data,
                        'download_history_parameters'),
                    history_data={},
                    downloaded_intervals=[]
                )

                is_new = self.database_context.is_exporter_code_unique(exporter.unique_code, None)

                history_data: typing.List[InstrumentValue, ...] = []
                for history_data_item in exporter_data['history_data']:
                    moment_str = history_data_item['moment']
                    value_float = history_data_item['value']

                    history_data.append(InstrumentValue(
                        moment=datetime.datetime.fromisoformat(moment_str),
                        value=decimal.Decimal(repr(value_float) if isinstance(value_float, float) else value_float)))

                downloaded_intervals: typing.List[typing.Tuple[datetime.date, datetime.date], ...] = []
                for downloaded_intervals_item in exporter_data['downloaded_intervals']:
                    date_from_str = downloaded_intervals_item['date_from']
                    date_to_str = downloaded_intervals_item['date_to']

                    downloaded_intervals.append(
                        (datetime.date.fromisoformat(date_from_str),
                         datetime.date.fromisoformat(date_to_str))
                    )

                settings_items[exporter.unique_code] = JsonImportSettingsItem(
                    exporter_unique_code=exporter.unique_code,
                    exporter=exporter,
                    is_new=is_new,
                    history_data=tuple(history_data),
                    downloaded_intervals=tuple(downloaded_intervals)
                )

        settings_data = JsonImportSettingsData(
            file_name=settings_file_name,
            file_content=settings_file_content,
            items=tuple(settings_items.values())
        )

        self.logger.info(f"Settings deserialized from {settings_file_name}")

        return settings_data

    def save_settings_data(
            self,
            settings_data: JsonImportSettingsData,
            exporters_codes: typing.Iterable[str],
            history_data_codes: typing.Iterable[str],
            downloaded_intervals_codes: typing.Iterable[str]):
        assert isinstance(settings_data, JsonImportSettingsData)

        self.logger.info("Save settings data")

        settings_items_by_exporter_code: typing.Dict[str, JsonImportSettingsItem] = {
            settings_item.exporter_unique_code: settings_item
            for settings_item
            in settings_data.items}

        with transaction.atomic(savepoint=False):
            for selected_exporter_code in exporters_codes:
                self.database_context.update_or_create(settings_items_by_exporter_code[selected_exporter_code].exporter)

            all_db_exporters = {exporter_model.unique_code: exporter_model
                                for exporter_model
                                in self.database_context.get_all_exporters_as_model()}

            # save history data
            for selected_exporter_code in history_data_codes:
                exporter = all_db_exporters[selected_exporter_code]
                history_data = settings_items_by_exporter_code[selected_exporter_code].history_data
                self.database_context.update_or_create_history_data(exporter, history_data)

            # save downloaded intervals
            for selected_exporter_code in downloaded_intervals_codes:
                exporter = all_db_exporters[selected_exporter_code]
                downloaded_intervals = settings_items_by_exporter_code[selected_exporter_code].downloaded_intervals
                self.database_context.update_or_create_downloaded_intervals(exporter, downloaded_intervals)

        self.logger.info("Settings data saved")


class SettingsManager:
    managers: typing.Dict[str, SpecificSettingsManager] = {
        '.xml': DjangoXmlSettingsManager(),
        '.json': JsonSettingsManager()
    }

    def serialize_settings(
            self,
            file_name: str,
            exporters: typing.Collection[view_models.Exporter],
            target_stream) -> typing.Optional[str]:
        extension = pathlib.Path(file_name).suffix.lower()
        if extension not in self.managers:
            raise ValueError(f"Unknown settings file extension {extension!r}")

        manager = self.managers[extension]
        return manager.serialize_settings(file_name, exporters, target_stream)

    def deserialize_settings(
            self,
            settings_file_name: str,
            settings_file_content: str) -> ImportSettingsData:
        extension = pathlib.Path(settings_file_name).suffix.lower()
        if extension not in self.managers:
            raise ValueError(f"Unknown settings file extension {extension!r}")

        manager = self.managers[extension]
        return manager.deserialize_settings(settings_file_name, settings_file_content)

    def save_settings_data(
            self,
            settings_data: ImportSettingsData,
            exporters_codes: typing.Iterable[str],
            history_data_codes: typing.Iterable[str],
            downloaded_intervals_codes: typing.Iterable[str]):
        extension = pathlib.Path(settings_data.file_name).suffix.lower()
        if extension not in self.managers:
            raise ValueError(f"Unknown settings file extension {extension!r}")

        manager = self.managers[extension]
        manager.save_settings_data(
            settings_data,
            exporters_codes,
            history_data_codes,
            downloaded_intervals_codes)
