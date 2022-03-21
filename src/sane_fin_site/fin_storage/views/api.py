import datetime
import decimal
import logging

from django.http import HttpResponseBadRequest, JsonResponse, HttpResponseNotFound
from django.utils import timezone
from django.views import generic
from sane_finances.sources import computing
from sane_finances.sources.base import InstrumentValue

from .. import db
from .. import models


class JsonHistoryDataView(generic.TemplateView):
    """
    Example:
        http://127.0.0.1:8000/api/v1/history/MSCI_World_NET_USD/2021-01-02/2021-08-01/?intraday=no&fill_gaps=no
    """
    intraday_query_param_name = 'intraday'
    fill_gaps_query_param_name = 'fill_gaps'

    date_format = '%Y-%m-%d'
    moment_format = '%Y-%m-%d %H:%M:%S'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

    def render_to_response(self, context, **response_kwargs):
        exporter_code, date_from, date_to = self.kwargs['code'], self.kwargs['date_from'], self.kwargs['date_to']
        moment_from = datetime.datetime.combine(date_from, datetime.time.min, tzinfo=timezone.get_current_timezone())
        moment_to = datetime.datetime.combine(date_to, datetime.time.min, tzinfo=moment_from.tzinfo)

        intraday = str(self.request.GET.get(self.intraday_query_param_name, 'no')).lower()
        fill_gaps = str(self.request.GET.get(self.fill_gaps_query_param_name, 'no')).lower()

        self.logger.info(f"Try to get JSON instrument history data for {exporter_code!r} "
                         f"{date_from.isoformat()}..{date_to.isoformat()}, "
                         f"intraday={intraday!r}, fill_gaps={fill_gaps!r}")

        intraday = {'yes': True, 'no': False}.get(intraday, None)
        if intraday is None:
            self.logger.error("Bad intraday value")
            return HttpResponseBadRequest()

        fill_gaps = {'yes': True, 'no': False}.get(fill_gaps, None)
        if fill_gaps is None:
            self.logger.error("Bad fill_gaps value")
            return HttpResponseBadRequest()

        # noinspection PyUnresolvedReferences
        try:
            exporter = db.DatabaseContext().get_exporter_by_code(exporter_code)
        except models.Exporter.DoesNotExist:
            self.logger.error(f"Exporter {exporter_code!r} not found")
            return HttpResponseNotFound()

        history_data = exporter.history_data

        data = []
        result = {
            'exporter_code': exporter.unique_code,
            'date_from': date_from.strftime(self.date_format),
            'date_to': date_to.strftime(self.date_format),
            'data': data
        }

        if date_from > date_to or not history_data:
            return JsonResponse(result)

        interval_data_type = (computing.IntervalHistoryDataValuesType.EVERY_DAY_VALUES
                              if fill_gaps
                              else computing.IntervalHistoryDataValuesType.ONLY_INTERIOR_VALUES)

        result_data = computing.build_sorted_history_data(
            history_data.values(),
            moment_from,
            moment_to,
            interval_data_type=interval_data_type,
            intraday=intraday)

        moment_format = self.moment_format if intraday else self.date_format

        data.extend([{'moment': moment.strftime(moment_format),
                      'value': value.value}
                     for moment, value
                     in result_data])

        return JsonResponse(result)


class JsonComposeDataView(generic.TemplateView):
    """
    Example:
        http://127.0.0.1:8000/api/v1/compose/MSCI_World_NET_USD/multiply/CBR.USD/2021-01-02/2021-08-01/?intraday=no&fill_gaps=no
    """
    intraday_query_param_name = 'intraday'
    fill_gaps_query_param_name = 'fill_gaps'

    date_format = '%Y-%m-%d'
    moment_format = '%Y-%m-%d %H:%M:%S'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

    def render_to_response(self, context, **response_kwargs):
        exporter1_code, exporter2_code = self.kwargs['code1'], self.kwargs['code2']
        compose_type_string = self.kwargs['compose_type']
        date_from, date_to = self.kwargs['date_from'], self.kwargs['date_to']
        moment_from = datetime.datetime.combine(date_from, datetime.time.min, tzinfo=timezone.get_current_timezone())
        moment_to = datetime.datetime.combine(date_to, datetime.time.min, tzinfo=moment_from.tzinfo)

        intraday = str(self.request.GET.get(self.intraday_query_param_name, 'no')).lower()
        fill_gaps = str(self.request.GET.get(self.fill_gaps_query_param_name, 'no')).lower()

        self.logger.info(f"Try to get JSON composed history data "
                         f"for {exporter1_code!r} {compose_type_string} {exporter2_code!r}"
                         f"{date_from.isoformat()}..{date_to.isoformat()}, "
                         f"intraday={intraday!r}, fill_gaps={fill_gaps!r}")

        intraday = {'yes': True, 'no': False}.get(intraday, None)
        if intraday is None:
            self.logger.error("Bad intraday value")
            return HttpResponseBadRequest()

        fill_gaps = {'yes': True, 'no': False}.get(fill_gaps, None)
        if fill_gaps is None:
            self.logger.error("Bad fill_gaps value")
            return HttpResponseBadRequest()

        compose_type = computing.ComposeType(compose_type_string)

        # noinspection PyUnresolvedReferences
        try:
            exporter1 = db.DatabaseContext().get_exporter_by_code(exporter1_code)
        except models.Exporter.DoesNotExist:
            self.logger.error(f"Exporter {exporter1_code!r} not found")
            return HttpResponseNotFound()
        # noinspection PyUnresolvedReferences
        try:
            exporter2 = db.DatabaseContext().get_exporter_by_code(exporter2_code)
        except models.Exporter.DoesNotExist:
            self.logger.error(f"Exporter {exporter2_code!r} not found")
            return HttpResponseNotFound()

        data = []
        result = {
            'exporter1_code': exporter1.unique_code,
            'exporter2_code': exporter2.unique_code,
            'date_from': date_from.strftime(self.date_format),
            'date_to': date_to.strftime(self.date_format),
            'compose_type': compose_type_string,
            'data': data
        }
        if date_from > date_to:
            return JsonResponse(result)

        interval_data_type = (computing.IntervalHistoryDataValuesType.EVERY_DAY_VALUES
                              if fill_gaps
                              else computing.IntervalHistoryDataValuesType.ALLOW_PRECEDING_VALUE)

        # noinspection PyUnusedLocal
        def _return_stub_error_handler(
                ex: Exception,
                compose_operation_type: computing.ComposeType,
                moment: datetime.datetime,
                left_value: InstrumentValue,
                right_value: InstrumentValue) -> decimal.Decimal:
            error_stub_value = decimal.Decimal(0)
            return error_stub_value

        composed_data = computing.build_composed_sorted_history_data(
            exporter1.history_data.values(),
            exporter2.history_data.values(),
            compose_type,
            moment_from,
            moment_to,
            interval_data_type=interval_data_type,
            intraday=intraday,
            compose_error_handler=_return_stub_error_handler
        )

        moment_format = self.moment_format if intraday else self.date_format
        data.extend(({'moment': moment.strftime(moment_format),
                      'value': value}
                     for moment, value
                     in composed_data))

        return JsonResponse(result)
