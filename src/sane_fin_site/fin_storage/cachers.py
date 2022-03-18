import collections
import datetime
import typing

from django.db import models, transaction
from django.utils import timezone
from sane_finances.communication.cachers import ExpirableCacher, ExpiryCalculator
from sane_finances.communication.url_downloader import UrlDownloader
from sane_finances.sources.base import (
    InstrumentExporterRegistry, AnyInstrumentInfoProvider, InstrumentValue, InstrumentExporterFactory,
    DownloadParameterValuesStorage)
from sane_finances.sources.generic import get_all_instrument_exporters

from .models import CachedItem
from .view_models import Exporter


class DjangoExpiryCalculator(ExpiryCalculator):
    """ Expiry calculator based on Django timezone """

    def is_expired(self, expiry_moment: datetime.datetime) -> bool:
        return timezone.now() > expiry_moment

    def get_expiry_moment(self, delta: datetime.timedelta, start_from: datetime.datetime = None) -> datetime.datetime:
        if start_from is None:
            start_from = timezone.now()
        return start_from + delta

    def get_revive_moment(self) -> datetime.datetime:
        return timezone.now()


class DjangoDbCacher(ExpirableCacher):
    """ Expirable cacher based on Django DB
    """

    _expiry: datetime.timedelta = datetime.timedelta(days=1)

    def __init__(self, expiry_calculator: ExpiryCalculator = None):
        self.expiry_calculator = DjangoExpiryCalculator() if expiry_calculator is None else expiry_calculator

    def has(self, url: str, parameters: typing.List[typing.Tuple[str, str]], headers: typing.Dict[str, str]) -> bool:
        return self._queryset().filter(url=url, parameters=parameters, headers=headers).exists()

    def is_empty(self) -> bool:
        return self._queryset().exists()

    @property
    def expiry(self):
        return self._expiry

    @expiry.setter
    def expiry(self, delta: datetime.timedelta):
        self._expiry = delta
        self.clean()

    @staticmethod
    def _queryset() -> models.QuerySet:
        # noinspection PyUnresolvedReferences
        return CachedItem.objects

    @staticmethod
    def _build_key(
            url: str,
            parameters: typing.List[typing.Tuple[str, str]],
            headers: typing.Dict[str, str]):
        return (str(url),
                ','.join([f"{param_name}={param_value}" for param_name, param_value in parameters]),
                ','.join([f"{header_name}:{header_value}" for header_name, header_value in headers.items()]))

    def clean(self):
        cached_item: typing.Optional[CachedItem]
        with transaction.atomic():
            for cached_item in self._queryset().all():
                # noinspection PyTypeChecker
                cached_item_revive_moment: datetime.datetime = cached_item.revive_moment
                new_expiry_moment = self.expiry_calculator.get_expiry_moment(
                    self.expiry,
                    start_from=cached_item_revive_moment)

                if (self.expiry_calculator.is_expired(cached_item.expiry_moment)
                        or self.expiry_calculator.is_expired(new_expiry_moment)):
                    cached_item.delete()

                else:
                    cached_item.expiry_moment = new_expiry_moment
                    cached_item.save()

    def retrieve(
            self,
            url: str,
            parameters: typing.List[typing.Tuple[str, str]],
            headers: typing.Dict[str, str],
            reviver: typing.Callable[[], str]) -> typing.Tuple[bool, str]:
        """ Try to find string by parameters inside the internal storage.
        If not found, then call reviver and store it result.

        Return pair: (got_from_cache, result)
        """
        self.clean()

        url, parameters, headers = self._build_key(url, parameters, headers)

        cached_item: typing.Optional[CachedItem]
        # noinspection PyUnresolvedReferences
        try:
            cached_item = self._queryset().get(url=url, parameters=parameters, headers=headers)
        except CachedItem.DoesNotExist:
            result = None
            cached_item = None
            got_from_cache = False
        else:
            result = cached_item.result
            got_from_cache = True

        # noinspection PyTypeChecker
        need_update = cached_item is None or self.expiry_calculator.is_expired(cached_item.expiry_moment)

        if need_update:
            result = reviver()
            revive_moment = self.expiry_calculator.get_revive_moment()
            expiry_moment = self.expiry_calculator.get_expiry_moment(self.expiry)

            create_attrs = {
                'url': url,
                'parameters': parameters,
                'headers': headers,
                'result': result,
                'revive_moment': revive_moment,
                'expiry_moment': expiry_moment}
            _ = self._queryset().create(**create_attrs)

        return got_from_cache, result

    def drop(
            self,
            url: str,
            parameters: typing.List[typing.Tuple[str, str]],
            headers: typing.Dict[str, str]) -> bool:
        self.clean()

        url, parameters, headers = self._build_key(url, parameters, headers)
        deleted, _ = self._queryset().filter(url=url, parameters=parameters, headers=headers).delete()

        return deleted != 0

    def full_clear(self):
        self._queryset().all().delete()


class StaticDataCache:
    """ Stores cached data.
    I.e. registered exporters, available instruments, etc.
    """
    _available_exporters_registries: typing.OrderedDict[int, InstrumentExporterRegistry] = None
    _available_instruments: typing.Dict[typing.Tuple, typing.OrderedDict[str, AnyInstrumentInfoProvider]] = {}
    _history_data: typing.Dict[typing.Tuple, typing.Iterable[InstrumentValue]] = {}
    _parameter_values_storage_cache = {}

    @classmethod
    def get_available_exporters_registries(cls):
        """ Guarantees that returned dictionary will be ordered in the same way
        and will have same keys (identities) during all program session
        (i.e. until web-server will be restarted)
        """
        if cls._available_exporters_registries is None:
            cls._available_exporters_registries = \
                collections.OrderedDict(enumerate(get_all_instrument_exporters(), start=1))

        return cls._available_exporters_registries

    @classmethod
    def download_available_instruments(
            cls,
            cache_key: typing.Tuple,
            download_info_parameters: typing.Any,
            exporter_factory: InstrumentExporterFactory) -> typing.OrderedDict[str, AnyInstrumentInfoProvider]:
        """ Download all available instruments for exporter factory and store it in cache
        """
        info_exporter = exporter_factory.create_info_exporter(UrlDownloader(DjangoDbCacher()))
        info_providers = info_exporter.export_instruments_info(download_info_parameters)

        instruments = collections.OrderedDict({
            info_provider.instrument_info.code: info_provider
            for info_provider
            in info_providers})

        cls._available_instruments[cache_key] = instruments

        return instruments

    @classmethod
    def get_available_instruments(
            cls,
            cache_key: typing.Tuple) -> typing.Optional[typing.OrderedDict[str, AnyInstrumentInfoProvider]]:
        """ Get all available instruments from cache
        """
        return cls._available_instruments.get(cache_key, None)

    @classmethod
    def download_history_data(
            cls,
            exporter: Exporter,
            moment_from: datetime.datetime,
            moment_to: datetime.datetime) -> typing.Iterable[InstrumentValue]:
        """ Download history data for exporter factory and store it in cache
        """
        history_exporter = exporter.exporter_registry.factory.create_history_values_exporter(
            UrlDownloader(DjangoDbCacher()))
        history_values = history_exporter.export_instrument_history_values(
            exporter.download_history_parameters,
            moment_from,
            moment_to)
        history_data = [history_value.get_instrument_value(tzinfo=moment_from.tzinfo)
                        for history_value
                        in history_values]

        cache_key = (exporter.id, moment_from, moment_to)
        cls._history_data[cache_key] = history_data

        return history_data

    @classmethod
    def get_history_data(
            cls,
            exporter: Exporter,
            moment_from: datetime.datetime,
            moment_to: datetime.datetime) -> typing.Optional[typing.Iterable[InstrumentValue]]:
        """ Get history data from cache
        """
        cache_key = (exporter.id, moment_from, moment_to)
        return cls._history_data.get(cache_key, None)

    @classmethod
    def drop_history_data_from_cache(
            cls,
            exporter: Exporter,
            moment_from: datetime.datetime,
            moment_to: datetime.datetime):
        """ Drop data from internal cache
        """
        cache_key = (exporter.id, moment_from, moment_to)
        if cache_key in cls._history_data:
            del cls._history_data[cache_key]

    @classmethod
    def download_parameter_values_storage(
            cls,
            instrument_exporter_factory: InstrumentExporterFactory) -> DownloadParameterValuesStorage:
        """ Create or get from internal cache ``DownloadParameterValuesStorage``
        """
        if instrument_exporter_factory not in cls._parameter_values_storage_cache:
            downloader = UrlDownloader(DjangoDbCacher())

            parameter_values_storage = \
                instrument_exporter_factory.create_download_parameter_values_storage(downloader)
            parameter_values_storage.reload()
            cls._parameter_values_storage_cache[instrument_exporter_factory] = parameter_values_storage

        return cls._parameter_values_storage_cache[instrument_exporter_factory]
