import logging
import io
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class FinStorageConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fin_storage'

    # noinspection PyMethodMayBeStatic
    def _stdout(self, stdout_io: io.TextIOBase, text: str):
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return

        stdout_io.write(text)

    def ready(self):
        from django.contrib.sessions.management.commands.clearsessions import Command

        logger.info("Clearing expired sessions...")
        try:
            clearsessions = Command()

            self._stdout(clearsessions.stdout, "Clearing expired sessions...")
            clearsessions.handle()
            self._stdout(clearsessions.stdout, "Expired sessions cleared successfully")

        except Exception as x:
            logger.exception("Error while clearing expired sessions", exc_info=x)

        else:
            logger.info("Expired sessions cleared successfully")
