@rem Manually run Django server

@call activate_venv.bat

@echo Run server for Python:
@python --version

@python ..\src\sane_fin_site\manage.py runserver --noreload localhost:8000
