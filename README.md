# README.md

## Project Description

The `iq` project is a Django application designed to manage multiple tenants and provide authentication services. This project structure includes two main apps: `tenants` for managing tenant-specific data and `authentication` for handling user authentication.

## Installation Instructions

### Prerequisites

- Python 3.9 or higher
- pip (Python package installer)

### Step-by-Step Installation

1. **Clone the Repository**
   ```sh
   git clone https://github.com/yourusername/iq.git
   cd iq
   ```

2. **Create a Virtual Environment (Recommended)**
   ```sh
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install Dependencies**
   ```sh
   pip install -r requirements.txt
   ```

4. **Migrate the Database**
   ```sh
   python manage.py migrate
   ```

5. **Run the Development Server**
   ```sh
   python manage.py runserver
   ```

6. **Access the Application**
   Open your web browser and navigate to `http://127.0.0.1:8000/`.

## Project Structure

The project is organized as follows:

- `config`: The main Django configuration directory.
  - `asgi.py`: ASGI entry-point for deploying with ASGI-compatible servers like Daphne or Uvicorn.
  - `settings.py`: Configuration settings for the Django project.
  - `urls.py`: URL declarations for the project.
  - `wsgi.py`: WSGI entry-point for deploying with WSGI-compatible servers like Gunicorn.

- `tenants`: Django app for managing tenant-specific data.
  - `models.py`: Tenant-related models.
  - `views.py`: Tenant-related views.
  - `urls.py`: URL declarations for the tenants app.
  - `apps.py`: App configuration class.

- `authentication`: Django app for handling user authentication.
  - `admin.py`: Admin site configurations.
  - `apps.py`: App configuration class.
  - `models.py`: Authentication models.
  - `tests.py`: Authentication tests.
  - `views.py`: Authentication views.

## Directory Structure

```
iq/
├── config/
│   ├── asgi.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── tenants/
│   ├── __init__.py
│   ├── models.py
│   ├── tests.py
│   ├── views.py
│   └── urls.py
├── authentication/
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── tests.py
│   └── views.py
├── manage.py
├── requirements.txt
└── .gitignore
```

## .gitignore

```
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# Distribution / packaging
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg

# PyInstaller
#  Usually these files are written by a python script from a template
#  before PyInstaller builds the exe, so as to inject date/other infos into it.
*.manifest
*.spec

# Installer logs
pip-log.txt
pip-delete-this-directory.txt

# Unit test / coverage reports
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
.hypothesis/
.pytest_cache/
```

## New File: requirements.txt

This file contains all the required packages for the `iq` project. To update this file, run:

```sh
pip freeze > requirements.txt
```

The contents of the `requirements.txt` file will be automatically generated based on the current virtual environment.