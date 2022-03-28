@rem Create new virtual environment

@echo Create virtual environment

@py -3.9 -m venv ..\venv

@echo # created by create_venv.bat script > ..\venv\.gitignore
@echo * >> ..\venv\.gitignore
