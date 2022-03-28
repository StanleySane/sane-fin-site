@rem Deactivate virtual environment

@if "%1"=="NO_ECHO" (@set _NO_ECHO="Y")

@if "%_NO_ECHO%"=="" (
    @echo Deactivate virtual environment
)

@call ..\venv\Scripts\deactivate.bat

@if "%_NO_ECHO%"=="" (
    @echo Virtual environment deactivated
)
