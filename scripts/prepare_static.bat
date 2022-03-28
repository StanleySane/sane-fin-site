@rem Prepare static site data

@call activate_venv.bat


@echo Collect static files

@python ..\src\sane_fin_site\manage.py collectstatic --clear --noinput
