from django.contrib import admin

from .models import (
    Exporter, InstrumentValue, DownloadedInterval, CachedItem, SourceApiActuality)

admin.site.register(Exporter)
admin.site.register(InstrumentValue)
admin.site.register(DownloadedInterval)
admin.site.register(CachedItem)
admin.site.register(SourceApiActuality)
