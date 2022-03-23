@rem Activate virtual environment

@if "%1"=="NO_ECHO" (@set _NO_ECHO="Y")

@if "%_NO_ECHO%"=="" (
    @echo Activate virtual environment
)

@call ..\venv\Scripts\activate.bat

@if "%_NO_ECHO%"=="" (
    @echo Virtual environment in %VIRTUAL_ENV% activated for Python:
    @python --version
    @echo.
)
