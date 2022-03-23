@rem Create and initialize virtual environment

@call create_venv.bat


@echo Initialize virtual environment

@call activate_venv.bat

@python -m pip install --upgrade pip
@python -m pip install -r ..\requirements.txt

@echo.
@echo Virtual environment initialization finished successfully
@echo.
