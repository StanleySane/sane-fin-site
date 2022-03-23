@rem Script for Django database initialization
@rem Run this script only once (!) over empty (!) database
@rem Make sure of using correct Python version

@call activate_venv.bat

@cd ..\src\sane_fin_site\

@echo Start database initialization for Python:
@python --version

@python manage.py migrate || goto :SomeError

@echo.
@echo Create admin user
@set DJANGO_SUPERUSER_PASSWORD=admin
@python manage.py createsuperuser --noinput --username admin --email admin@example.com || goto :SomeError

@echo.
@echo Initialization finished successfully
@echo.

@goto :Finish

:SomeError
@echo.
@echo Finished with ERRORS (see above)
@echo.

:Finish