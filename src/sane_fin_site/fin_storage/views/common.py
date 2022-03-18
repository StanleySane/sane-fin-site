import typing
import datetime

from django import forms
from sane_finances.inspection import analyzers, serialize
from sane_finances.sources.base import DownloadParameterValuesStorage

from ..form_fields_managers import FormFieldsManager

T = typing.TypeVar('T')


def all_pages_context():
    return {
        'site_title': 'Sane Finances Storage',
        'site_header': 'Sane Finances Storage',
        'site_url': '/',
        'has_permission': False,
        'is_popup': False,
        'is_nav_sidebar_enabled': False,
    }


def form_as_div(form: forms.BaseForm):
    # noinspection PyProtectedMember
    return form._html_output(
        normal_row='<div class="form-row field-%(field_name)s %(css_classes)s">%(errors)s'
                   '<div>%(label)s %(field)s%(help_text)s</div>'
                   '</div>',
        error_row='%s',
        row_ender='</div>',
        help_text_html=' <span class="helptext">%s</span>',
        errors_on_separate_row=False,
    )


def java_script_date_str(moment: datetime.date):
    return f"new Date({moment.year},{moment.month - 1},{moment.strftime('%d,%H,%M,%S')})"


class SpecificInstanceManagersPack:
    instance_builder: analyzers.InstanceBuilder
    factory_converter: analyzers.InstanceFactoryDataConverter
    instance_flattener: analyzers.InstanceFlattener
    form_fields_manager: FormFieldsManager
    serializer: serialize.FlattenedDataJsonSerializer

    def __init__(
            self,
            download_param_values_storage: DownloadParameterValuesStorage,
            root_data_class: typing.Type[T],
            root_factory: typing.Callable[..., T],
            attr_name_prefix: str):
        instance_analyzer = analyzers.FlattenedAnnotatedInstanceAnalyzer(
            root_data_class,
            download_param_values_storage,
            attr_name_prefix)
        self.factory_converter = analyzers.InstanceFactoryDataConverter(instance_analyzer)
        self.instance_flattener = analyzers.InstanceFlattener(instance_analyzer, download_param_values_storage)
        self.instance_builder = analyzers.InstanceBuilder(
            root_factory,
            download_param_values_storage)
        self.form_fields_manager = FormFieldsManager(
            instance_analyzer,
            download_param_values_storage)
        self.serializer = serialize.FlattenedDataJsonSerializer(instance_analyzer, download_param_values_storage)
