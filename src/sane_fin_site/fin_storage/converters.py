import re
import datetime

from sane_finances.sources import computing


class IsoDateConverter:
    regex = '([0-9]{4})-([0-9]{2})-([0-9]{2})'

    date_pattern = re.compile(regex)

    # noinspection PyMethodMayBeStatic
    def to_python(self, value):
        m = self.date_pattern.match(value)
        if not m:
            raise ValueError(f"Value {value!r} not matched to pattern")

        year, month, day = map(int, m.groups())
        date = datetime.date(year, month, day)

        return date

    # noinspection PyMethodMayBeStatic
    def to_url(self, value: datetime.date):
        return value.strftime('%Y-%m-%d')


class ComposeTypeConverter:
    regex = '|'.join([compose_type.value for compose_type in computing.ComposeType])

    # noinspection PyMethodMayBeStatic
    def to_python(self, value):
        return value

    # noinspection PyMethodMayBeStatic
    def to_url(self, value: str):
        return value
