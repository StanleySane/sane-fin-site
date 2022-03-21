import logging
import typing

from django.contrib import messages
from django.contrib.admin import helpers
from django.db import models as django_models
from django.http import HttpResponseRedirect
from django.http.response import HttpResponseBase, HttpResponse
from django.urls import reverse_lazy
from django.views import generic

from .common import all_pages_context
from .. import apps
from .. import db
from ..storage_manage import SettingsManager
from ..view_models import Exporter


class ExportersView(generic.list.ListView):
    template_name = apps.FinStorageConfig.name + '/exporters_list.html'
    action_form = helpers.ActionForm

    object_list: typing.List[Exporter]
    title = 'Exporters List'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__ + '.' + self.__class__.__name__)

        self.settings_manager = SettingsManager()

    def get_queryset(self):
        return list(db.DatabaseContext().get_all_exporters())

    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def on_export_django_xml(self, request, exporters: typing.List[Exporter]):
        file_name = 'exporters.xml'
        response = HttpResponse(
            content_type='text/xml',
            headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
        )

        _ = self.settings_manager.serialize_settings(file_name, exporters, response)
        return response

    # noinspection PyMethodMayBeStatic,PyUnusedLocal
    def on_export_json(self, request, exporters: typing.List[Exporter]):
        file_name = 'exporters.json'
        exporters_as_str = self.settings_manager.serialize_settings(file_name, exporters, None)

        response = HttpResponse(
            content=exporters_as_str.encode(),
            content_type='text/json',
            headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
        )

        return response

    # noinspection PyUnusedLocal
    def get_actions(self, request):
        """ Return a dictionary mapping the names of all actions for this
        view to a tuple of (callable, name, description) for each action.
        """
        actions = [
            (self.on_export_django_xml, "export_django_xml", "Export settings and history data to XML"),
            (self.on_export_json, "export_json", "Export exporters and history data to JSON")
        ]
        return {name: (func, name, desc) for func, name, desc in actions}

    def get_action_choices(self, request, default_choices=django_models.BLANK_CHOICE_DASH):
        """ Return a list of choices for use in a form object.
        Each choice is a tuple (name, description).
        """
        choices = [] + default_choices
        for func, name, description in self.get_actions(request).values():
            choice = (name, description)
            choices.append(choice)
        return choices

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        context_data.update(all_pages_context())

        action_form = self.action_form(auto_id=None)
        action_form.fields['action'].choices = self.get_action_choices(self.request)

        context_data.update({
            'title': self.title,
            'subtitle': None,
            'action_form': action_form,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'selection_note': f"0 of {len(self.object_list)} selected"
        })

        return context_data

    # noinspection PyMethodMayBeStatic
    def get_success_url(self):
        """Return the URL to redirect to after processing a valid form."""
        return reverse_lazy(apps.FinStorageConfig.name + ':exporters', current_app=apps.FinStorageConfig.name)

    # noinspection PyUnusedLocal
    def post(self, request, *args, **kwargs):
        """
        Handle POST requests: instantiate a form instance with the passed
        POST variables and then check if it's valid.
        """
        self.logger.info("Do action over selected exporters")

        self.object_list = object_list = self.get_queryset()

        action_index = int(request.POST.get('index', 0))

        # Construct the action form.
        data = request.POST.copy()
        data.pop(helpers.ACTION_CHECKBOX_NAME, None)
        data.pop("index", None)

        # Use the action whose button was pushed
        data.update({'action': data.getlist('action')[action_index]})

        action_form = self.action_form(data, auto_id=None)
        action_form.fields['action'].choices = self.get_action_choices(request)

        # If the form's valid we can handle the action.
        if action_form.is_valid():
            action = action_form.cleaned_data['action']
            func, name, *_ = self.get_actions(request)[action]
            self.logger.info(f"Try to perform action {name!r}")

            select_across = action_form.cleaned_data['select_across']

            # Get the list of selected PKs. If nothing's selected, we can't
            # perform an action on it, so bail. Except we want to perform
            # the action explicitly on all objects.
            selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            if not selected and not select_across:
                # Reminder that something needs to be selected or nothing will happen
                messages.warning(self.request, "Items must be selected in order to perform "
                                               "actions on them. No items have been changed.")
                return HttpResponseRedirect(self.get_success_url())

            if not select_across:
                # Perform the action only on the selected objects
                object_list = [exporter for exporter in object_list if str(exporter.id) in selected]

            response = func(request, object_list)

            # Actions may return an HttpResponse-like object, which will be
            # used as the response from the POST. If not, we'll be a good
            # little HTTP citizen and redirect back to the changelist page.
            if isinstance(response, HttpResponseBase):
                return response

            messages.success(self.request, "Action finished successfully.")
            return HttpResponseRedirect(self.get_success_url())

        else:
            message = "No action selected."
            self.logger.info(message)
            messages.warning(self.request, message)
            return HttpResponseRedirect(self.get_success_url())

    # PUT is a valid HTTP verb for creating (with a known URL) or editing an
    # object, note that browsers only support POST for now.
    def put(self, *args, **kwargs):
        return self.post(*args, **kwargs)
