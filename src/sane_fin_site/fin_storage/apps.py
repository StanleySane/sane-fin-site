import logging
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class FinStorageConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fin_storage'

    # noinspection PyMethodMayBeStatic
    def _clear_sessions(self):
        from django.contrib.sessions.management.commands.clearsessions import Command

        logger.info("Clearing expired sessions...")
        try:
            clearsessions = Command()
            clearsessions.handle()

        except Exception as ex:
            logger.exception("Error while clearing expired sessions", exc_info=ex)

        else:
            logger.info("Expired sessions cleared successfully")

    def ready(self):
        if 'runserver' in sys.argv:
            self._clear_sessions()
