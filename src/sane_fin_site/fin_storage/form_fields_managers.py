import typing
import collections
import decimal
import enum
import datetime
import inspect

from django import forms
from django.contrib.admin import widgets
from django.db import models
from django.forms import utils

from sane_finances.inspection.analyzers import FlattenedInstanceAnalyzer, InstanceAttributeInfo
from sane_finances.sources.base import DownloadParameterValuesStorage
from sane_finances.annotations import SupportsDescription


class FormFieldsManager:
    """ Manager for form fields """
    # order is important: last item wins.
    # thus base classes have to locate at the beginning,
    # more specialized classes (subclasses) have to locate at the ending.
    # otherwise, base class will always be used.
    field_type_mapping: typing.OrderedDict[typing.Any,
                                           typing.Tuple[typing.Type[forms.Field], typing.Dict[str, typing.Any]]] = \
        collections.OrderedDict({
            str: (forms.CharField, {'widget': widgets.AdminTextInputWidget()}),
            int: (forms.IntegerField, {'widget': widgets.AdminIntegerFieldWidget()}),
            bool: (forms.BooleanField, {'required': False}),
            float: (forms.FloatField, {}),
            decimal.Decimal: (forms.DecimalField, {'max_digits': 50, 'decimal_places': 4}),
            datetime.date: (forms.DateField, {'widget': widgets.AdminDateWidget()}),
            datetime.datetime: (forms.SplitDateTimeField, {'widget': widgets.AdminSplitDateTime()}),
            enum.Enum: (forms.ChoiceField, {})
        })

    def __init__(
            self,
            flattened_instance_analyzer: FlattenedInstanceAnalyzer,
            parameter_values_storage: DownloadParameterValuesStorage):
        self.parameter_values_storage = parameter_values_storage

        self._prepare(flattened_instance_analyzer)

    @property
    def form_fields(self) -> typing.Dict[str, forms.Field]:
        return self._form_fields

    @property
    def immutable_form_fields(self) -> typing.FrozenSet[str]:
        return frozenset(self._immutable_form_fields)

    @property
    def instrument_identity_form_fields(self) -> typing.FrozenSet[str]:
        return frozenset(self._instrument_identity_form_fields)

    def _materialize_choice_value(self, field_value, attr_info: InstanceAttributeInfo):
        if self.parameter_values_storage.is_dynamic_enum_type(attr_info.origin_annotated_type):
            enum_value = self.parameter_values_storage.get_dynamic_enum_value_by_choice(
                attr_info.origin_annotated_type,
                field_value)

        else:
            assert issubclass(attr_info.origin_annotated_type, enum.Enum)
            enum_value = attr_info.origin_annotated_type(field_value)

        return enum_value

    def materialize_choice_values(
            self,
            form_cleaned_data: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
        """ Convert stringified choice values to original enum values
        """
        return {
            field_name: (self._materialize_choice_value(field_value, self._choice_fields[field_name])
                         if field_name in self._choice_fields
                         else field_value)
            for field_name, field_value
            in form_cleaned_data.items()
        }

    def _build_label(self, attr_info: InstanceAttributeInfo):
        original_attr_name = attr_info.path_from_root[-1]

        label = (attr_info.description_annotation.short_description
                 if attr_info.description_annotation
                 else utils.pretty_name(original_attr_name))

        if attr_info.parent_info is not None:
            parent_label = self._build_label(attr_info.parent_info)
            label = f"{parent_label}: {label}"

        return label

    def _prepare(self, flattened_instance_analyzer: FlattenedInstanceAnalyzer):
        self._form_fields: typing.Dict[str, forms.Field] = {}
        self._immutable_form_fields: typing.Set[str] = set()
        self._instrument_identity_form_fields: typing.Set[str] = set()
        self._choice_fields: typing.Dict[str, InstanceAttributeInfo] = {}

        enums_field_data = self.field_type_mapping.get(enum.Enum, None)
        if enums_field_data is None:
            raise ValueError("Not found field map for enums")

        field_type_mapping = (
                list(self.field_type_mapping.items()) +
                [(dynamic_enum_type, enums_field_data)
                 for dynamic_enum_type
                 in self.parameter_values_storage.get_all_managed_types()
                 if dynamic_enum_type not in self.field_type_mapping]
        )

        flattened_attrs_info = flattened_instance_analyzer.get_flattened_attrs_info()
        for flattened_attr_name, attr_info in flattened_attrs_info.items():
            label = self._build_label(attr_info)
            help_text = (attr_info.description_annotation.description
                         if attr_info.description_annotation
                         else None)

            last_matched_type = collections.deque(
                ((field_type, kwargs)
                 for attr_type, (field_type, kwargs)
                 in field_type_mapping
                 if (issubclass(attr_info.origin_annotated_type, attr_type)
                     if inspect.isclass(attr_info.origin_annotated_type) and inspect.isclass(attr_type)
                     else attr_info.origin_annotated_type == attr_type)
                 ),
                maxlen=1)

            if not last_matched_type:
                raise ValueError(f"Not found flattened attribute info for {flattened_attr_name!r} "
                                 f"with type {attr_info.origin_annotated_type}")

            field_type, kwargs = last_matched_type.pop()
            field = field_type(**kwargs)

            if label is not None:
                field.label = label

            if help_text is not None:
                field.help_text = help_text

            if attr_info.has_default:
                field.initial = attr_info.default_value
                if attr_info.default_value is None:
                    field.required = False

            if isinstance(field, forms.ChoiceField):
                choices = self.parameter_values_storage.get_parameter_type_choices(attr_info.origin_annotated_type)
                if choices is None:
                    # if choices not provided try to build them ourselves
                    if (inspect.isclass(attr_info.origin_annotated_type)
                            and issubclass(attr_info.origin_annotated_type, enum.Enum)):
                        # but it's possible only for enums
                        enum_value: enum.Enum
                        choices = [(enum_value.value,
                                    enum_value.description
                                    if isinstance(enum_value, SupportsDescription)
                                    else enum_value.name)
                                   for enum_value
                                   in attr_info.origin_annotated_type]

                if choices:
                    choices = tuple(models.BLANK_CHOICE_DASH) + tuple(choices)

                field.choices = choices or ()
                self._choice_fields[flattened_attr_name] = attr_info

            volatile = attr_info.volatile_annotation
            if volatile is not None:

                if volatile.stub_value is not None:
                    field.initial = volatile.stub_value
                    field.disabled = True

                else:
                    field.disabled = (
                            not field.required
                            or (attr_info.has_default and attr_info.default_value not in field.empty_values)
                    )

            self._form_fields[flattened_attr_name] = field

            if attr_info.is_immutable:
                self._immutable_form_fields.add(flattened_attr_name)

            if attr_info.instrument_info_parameter_annotation is not None \
                    and attr_info.instrument_info_parameter_annotation.instrument_identity:
                self._instrument_identity_form_fields.add(flattened_attr_name)


class ReadonlyFormFieldsManager(FormFieldsManager):
    """ Manager for form readonly fields
    """

    field_type_mapping: typing.OrderedDict[typing.Any,
                                           typing.Tuple[typing.Type[forms.Field], typing.Dict[str, typing.Any]]] = \
        collections.OrderedDict({
            str: (forms.CharField, {'disabled': True}),
            int: (forms.IntegerField, {'disabled': True}),
            bool: (forms.BooleanField, {'required': False, 'disabled': True}),
            float: (forms.FloatField, {'disabled': True}),
            decimal.Decimal: (forms.DecimalField, {'max_digits': 50, 'decimal_places': 4, 'disabled': True}),
            datetime.date: (forms.DateField, {'disabled': True}),
            datetime.datetime: (forms.SplitDateTimeField, {'disabled': True}),
            enum.Enum: (forms.ChoiceField, {'disabled': True})
        })
