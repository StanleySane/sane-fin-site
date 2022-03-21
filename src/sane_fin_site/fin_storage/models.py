from django.db import models

from . import apps


class ExporterManager(models.Manager):

    def get_by_natural_key(self, unique_code):
        return self.get(unique_code=unique_code)


class Exporter(models.Model):
    unique_code = models.CharField(max_length=128)
    description = models.TextField(max_length=256)
    is_active = models.BooleanField(default=True)
    exporter_type = models.TextField(max_length=2048)
    download_info_parameters = models.TextField(max_length=2048)
    download_history_parameters = models.TextField(max_length=2048)

    objects = ExporterManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['unique_code'], name='unique_code')]

    def natural_key(self):
        # noinspection PyRedundantParentheses
        return (self.unique_code,)

    def __str__(self):
        return (f"{self.__class__.__name__} "
                f"(id={self.pk}, "
                f"unique_code={self.unique_code}, "
                f"is_active={self.is_active}, "
                f"description={self.description}, "
                f"exporter_type={self.exporter_type})")


class InstrumentValueManager(models.Manager):

    def get_by_natural_key(self, exporter_unique_code, moment):
        # noinspection PyUnresolvedReferences
        try:
            exporter = Exporter.objects.get_by_natural_key(exporter_unique_code)
        except Exporter.DoesNotExist as ex:
            raise self.model.DoesNotExist() from ex

        return self.get(exporter_id=exporter.id, moment=moment)


class InstrumentValue(models.Model):
    moment = models.DateTimeField()
    value = models.DecimalField(max_digits=50, decimal_places=6)
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE, related_name='history_data')

    objects = InstrumentValueManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exporter', 'moment'], name='unique_exporter_moment')]

    def natural_key(self):
        # noinspection PyUnresolvedReferences
        exporter_natural_key = ((None,)
                                if (not hasattr(self, 'exporter') or self.exporter is None)
                                else self.exporter.natural_key())
        return exporter_natural_key + (self.moment,)

    natural_key.dependencies = [f'{apps.FinStorageConfig.name}.exporter']

    def __str__(self):
        # noinspection PyUnresolvedReferences
        return (f"{self.__class__.__name__} "
                f"(exporter={self.exporter_id}, "
                f"moment={self.moment}, "
                f"value={self.value})")


class DownloadedIntervalManager(models.Manager):

    def get_by_natural_key(self, exporter_unique_code, date_from):
        # noinspection PyUnresolvedReferences
        try:
            exporter = Exporter.objects.get_by_natural_key(exporter_unique_code)
        except Exporter.DoesNotExist:
            raise self.model.DoesNotExist()

        return self.get(exporter_id=exporter.id, date_from=date_from)


class DownloadedInterval(models.Model):
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE, related_name='downloaded_intervals')
    date_from = models.DateField()
    date_to = models.DateField()

    objects = DownloadedIntervalManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exporter', 'date_from'], name='unique_exporter_date_from')]

    def natural_key(self):
        # noinspection PyUnresolvedReferences
        exporter_natural_key = ((None,)
                                if (not hasattr(self, 'exporter') or self.exporter is None)
                                else self.exporter.natural_key())
        return exporter_natural_key + (self.date_from,)

    natural_key.dependencies = [f'{apps.FinStorageConfig.name}.exporter']

    def __str__(self):
        # noinspection PyUnresolvedReferences
        return (f"{self.__class__.__name__} "
                f"(exporter={self.exporter_id}, "
                f"date_from={self.date_from}, "
                f"date_to={self.date_to})")


class CachedItem(models.Model):
    url = models.CharField(max_length=255)
    parameters = models.CharField(max_length=255, blank=True)
    headers = models.CharField(max_length=255, blank=True)
    result = models.TextField(blank=True)
    revive_moment = models.DateTimeField()
    expiry_moment = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['url', 'parameters', 'headers'], name='UQ_url_param_headers')]

    def __str__(self):
        return (f"{self.__class__.__name__} "
                f"(url={self.url}, "
                f"parameters={self.parameters}, "
                f"headers={self.headers}, "
                f"revive_moment={self.revive_moment}, "
                f"expiry_moment={self.expiry_moment})")


class SourceApiActuality(models.Model):
    exporter_type = models.CharField(max_length=255, unique=True)
    check_error_message = models.TextField(null=True, blank=True)
    last_check_moment = models.DateTimeField()

    def __str__(self):
        return (f"{self.__class__.__name__} "
                f"(exporter_type={self.exporter_type}, "
                f"check_error_message={self.check_error_message}, "
                f"last_check_moment={self.last_check_moment})")
