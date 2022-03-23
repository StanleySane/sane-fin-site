@rem Create a bundled package by PyInstaller

@call activate_venv.bat


@echo Collect static files

@python ..\src\sane_fin_site\manage.py collectstatic --clear --noinput


@echo Install PyInstaller

@python -m pip install --upgrade pip
@python -m pip install --upgrade pyinstaller


@cd ..

@set EXE_NAME=sane_fin_site

@echo Generate temporary files
@if not exist .\scripts\tmp (@mkdir .\scripts\tmp)
@echo %EXE_NAME%.exe runserver --noreload localhost:8000 > .\scripts\tmp\runscript.bat
@echo Temporary files generated

@pyinstaller --name=%EXE_NAME% --clean ^
    --add-data "src/sane_fin_site/fin_storage/templates;fin_storage/templates" ^
    --add-data "src/sane_fin_site/static;static" ^
    --add-data "scripts/tmp/runscript.bat;." ^
    .\src\sane_fin_site\manage.py || goto :SomeError

@echo Remove temporary files
@rmdir .\scripts\tmp /S /Q
@echo Temporary files removed

@echo.
@echo PyInstaller finished successfully
@echo.

@goto :Finish

:SomeError
@echo.
@echo Finished with ERRORS (see above)
@echo.

:Finish