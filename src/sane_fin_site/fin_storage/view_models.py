import dataclasses
import datetime
import decimal
import typing

from django.utils import timezone
from sane_finances.inspection import analyzers
from sane_finances.sources.base import (
    InstrumentExporterRegistry, AnyInstrumentHistoryDownloadParameters, InstrumentValue)


@dataclasses.dataclass
class Exporter:
    """ View model for exporter
    """
    id: int
    unique_code: str
    description: str
    is_active: bool
    exporter_registry: typing.Optional[InstrumentExporterRegistry]
    download_info_parameters: typing.Any
    download_info_parameters_str: str
    download_history_parameters: typing.Optional[AnyInstrumentHistoryDownloadParameters]
    download_history_parameters_str: str
    history_data: typing.Dict[datetime.datetime, InstrumentValue]
    downloaded_intervals: typing.List[typing.Tuple[datetime.date, datetime.date]]
    raw_exporter_type: str = None
    error_message: str = None
    has_gaps: bool = False
    is_actual: bool = True

    @property
    def exporter_type(self):
        return (self.raw_exporter_type
                if self.exporter_registry is None
                else analyzers.get_full_path(self.exporter_registry.factory.__class__))

    @property
    def disabled(self):
        return bool(self.error_message)


@dataclasses.dataclass
class HistoryDataItem:
    """ View model for history data item in form
    """
    moment: datetime.datetime
    value: decimal.Decimal
    comment: str = None
    disabled: bool = False


@dataclasses.dataclass
class SourceApiActualityInfo:
    """ View model for source API actuality
    """
    id: int
    raw_exporter_type: typing.Optional[str]
    exporter_registry: typing.Optional[InstrumentExporterRegistry]
    check_error_message: typing.Optional[str]
    last_check_moment: typing.Optional[datetime.datetime]

    @property
    def exporter_type(self):
        return (self.raw_exporter_type
                if self.exporter_registry is None
                else analyzers.get_full_path(self.exporter_registry.factory.__class__))

    @property
    def status(self):
        if self.last_check_moment is None:
            return 'unknown'
        if self.check_error_message:
            return 'failed'
        return 'valid'

    @property
    def not_found(self):
        return self.exporter_registry is None

    @property
    def last_check_moment_str(self):
        if self.last_check_moment is None:
            return None

        now = timezone.now()
        spent = now - self.last_check_moment
        days = spent.days
        hours = int(spent.seconds / 60 / 60)
        minutes = int((spent.seconds - hours * 60 * 60) / 60)
        seconds = spent.seconds - hours * 60 * 60 - minutes * 60

        spent_str = ""
        if days:
            spent_str += f"{days} days "
        if hours or days:
            spent_str += f"{hours} hours "
        if minutes or hours or days:
            spent_str += f"{minutes} minutes "
        if seconds:
            spent_str += f"{seconds} seconds "
        spent_str += "ago"

        return self.last_check_moment.strftime('%d %b %Y %H:%M:%S') + ', ' + spent_str
