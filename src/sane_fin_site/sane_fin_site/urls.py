"""sane_fin_site URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
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
import re

from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path

from .views import static_handler

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('fin_storage.urls')),
]

if settings.SERVE_STATIC:
    urlpatterns += [
        re_path(r'^%s(?P<path>.*)$' % re.escape(settings.STATIC_URL.lstrip('/')),
                static_handler, kwargs={'document_root': settings.STATIC_ROOT}),
    ]
