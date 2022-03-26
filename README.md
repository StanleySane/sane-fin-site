# Sane Finances Site

[sane_finances]: https://github.com/StanleySane/sane-finances/
[py_installer]: https://pyinstaller.readthedocs.io/

A simple Web UI for managing financial data from various sources
with [Sane Finances][sane_finances] library.

## Install

### Requirements

For using this project you must have Python 3.9 (at least) installed on your system.

All following instructions assume that you have Windows OS and `py` Python starter installed.
You can modify provided scripts for using on any other OS or create your own with similar functionality.

### Prepare source and start working

1. Clone this repository or download ZIP file from GitHub to your local folder.

2. Move to `scripts` folder and start `create_venv.bat` script.
It will create `venv` directory for Python virtual environment.

    > Provided script calls Python 3.9 interpreter but you can modify it for using any other supported Python version.

3. Start `prepare_venv.bat` script.
It will download and install into `venv` all required packages and libraries.

    > Before continue you maybe need to fix some [security issues](#Security issues).

4. Start `intialize_db.bat` script.
It will initialize database due to `DATABASES` settings from `\src\sane_fin_site\sane_fin_site\settings.py` module.
All required tables and `admin` account will be created in the database.

   > Default database provider is SQLite.

5. Start `prepare_static.bat` script.
It will prepare required static files for using via HTTP
(see [How to manage static files](https://docs.djangoproject.com/en/4.0/howto/static-files/) for details).

   > You may choose another way to manage static files (e.g. via `Nginx` or `Apache`)
   > as described in [How to deploy static files](https://docs.djangoproject.com/en/4.0/howto/static-files/deployment/).
   > In such case you have to switch off built-in "static handler"
   > and set value of the `SERVE_STATIC` setting (from `\src\sane_fin_site\sane_fin_site\settings.py` module)
   > to `False`.

6. Now you can start `runserver.bat` to run [Django](https://www.djangoproject.com/) server,
open `localhost:8000` in your browser
and work with `Sane Finances Site`.

    > You can modify `runserver.bat` to start server on any other available port apart from `8000`.

The server and the site will be available until you press `CTRL+BREAK` in the console window
created by `runserver.bat` script (or just close that window).
All changes, exporters, downloaded data, etc. will be saved into database
and will be resumed after you start `runserver.bat` again. 

### Security issues

This project supposed to be used as simple (even primitive) Web UI for [Sane Finances][sane_finances] library
working on the local host (user machine).
It is not supposed to be used as HTTP server visible to the outer "wild" Internet.

But you can increase security level of the server if you want
(see [Django Deployment checklist](https://docs.djangoproject.com/en/4.0/howto/deployment/checklist/) for details).

> All following hints are not required if you will continue to use server for your own purposes on your local machine  

#### Debug mode

Ensure that value of `DEBUG` setting in the `\src\sane_fin_site\sane_fin_site\settings.py` module is `False`.

#### Secret key

Start `generate_new_secret_key.bat` script
and copy it's output into `SECRET_KEY` setting value in the `\src\sane_fin_site\sane_fin_site\settings.py` module.

#### Time zone

Change the value of `TIME_ZONE` setting in the `\src\sane_fin_site\sane_fin_site\settings.py` module
to your current location.

#### Admin account

While database initialization `admin` account created with default password and e-mail
(see `intialize_db.bat` script).
So, right after first start of the server you have to go to [Admin page](http://localhost:8000/admin/)
and change `admin` settings (e-mail and password).

### Bundled project

You can make your project "bundled" (i.e. packed into single file with all dependencies) and almost portable
via [PyInstaller][py_installer].

Just start `run_pyinstaller.bat` script.
After script completed (ignore all `Could not find GDAL library` errors)
you can find new folder `\dist\sane_fin_site` with all files needed for working as standalone application
without Python installed.

You can pack this folder into ZIP file and move to another machine with same OS.

> Such bundled application will work only on the same OS as it was created.
> See [PyInstaller][py_installer] for details.
