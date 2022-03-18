"""fin_storage URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, register_converter
from django.views.i18n import JavaScriptCatalog

from . import converters
from .views import (
    exporters_list, exporter_edit, exporter_detail, exporter_add, import_settings, api, sources_actuality)

register_converter(converters.IsoDateConverter, 'iso_date')
register_converter(converters.ComposeTypeConverter, 'compose_type')

app_name = 'fin_storage'

urlpatterns = [
    path('jsi18n/', JavaScriptCatalog.as_view(packages=['django.contrib.admin']), name='jsi18n'),
    path('', exporters_list.ExportersView.as_view(), name='home'),
    path('exporters/', exporters_list.ExportersView.as_view(), name='exporters'),
    path('exporters/import/', import_settings.ImportSettingsView.as_view(), name='import_settings'),
    path('exporters/add/', exporter_add.ExportersAddView.as_view(), name='exporters_add'),
    path('exporters/add/type<int:type_id>/<uuid:rand_id>/info/',
         exporter_edit.ExportersAddTypedInfoView.as_view(),
         name='exporters_add_typed_info'),
    path('exporters/add/type<int:type_id>/<uuid:rand_id>/params/',
         exporter_edit.ExportersAddTypedParamsView.as_view(),
         name='exporters_add_typed_params'),
    path('exporters/add/type<int:type_id>/<uuid:rand_id>/cancel/',
         exporter_edit.ExportersAddTypedCancelView.as_view(),
         name='exporters_add_typed_cancel'),
    path('exporters/<int:id>/params/', exporter_edit.ExportersEditParamsView.as_view(), name='exporters_edit_params'),
    path('exporters/<int:id>/info/', exporter_edit.ExportersEditInfoView.as_view(), name='exporters_edit_info'),
    path('exporters/<int:id>/delete/', exporter_edit.ExportersDeleteView.as_view(), name='exporters_delete'),
    path('exporters/<int:id>/', exporter_detail.ExportersDetailView.as_view(), name='exporters_detail'),
    path('exporters/<int:id>/actualize/',
         sources_actuality.ActualizeHistoryRedirectView.as_view(),
         name='actualize_history'),
    path('sources_actuality/', sources_actuality.SourceApiActualityView.as_view(), name='sources_actuality'),
    path(r'api/v1/history/<str:code>/<iso_date:date_from>/<iso_date:date_to>/',
         api.JsonHistoryDataView.as_view(),
         name='api_history'),
    path(r'api/v1/compose/<str:code1>/<compose_type:compose_type>/<str:code2>/<iso_date:date_from>/<iso_date:date_to>/',
         api.JsonComposeDataView.as_view(),
         name='api_compose'),
]
