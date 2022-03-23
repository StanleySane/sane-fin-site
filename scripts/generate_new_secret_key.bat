@rem Script for new random secret key generation
@rem Copy the output of this script into \src\sane_fin_site\sane_fin_site\settings.py in SECRET_KEY value

@call activate_venv.bat NO_ECHO

@python ..\src\sane_fin_site\manage.py shell --no-startup --command="from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"