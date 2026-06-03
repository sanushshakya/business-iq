# README.md

## Project Description

The `iq` project is a Django application designed to manage multiple tenants and provide authentication services. This project structure includes three main apps: `tenants`, `demand`, and `logistics`.

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

- `demand`: Django app for managing demand data.
  - `models.py`: Demand-related models.
  - `views.py`: Demand-related views.
  - `urls.py`: URL declarations for the demand app.
  - `apps.py`: App configuration class.

- `logistics`: Django app for managing logistics data.
  - `models.py`: Logistics-related models.
  - `views.py`: Logistics-related views.
  - `urls.py`: URL declarations for the logistics app.
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
├── demand/
│   ├── __init__.py
│   ├── models.py
│   ├── tests.py
│   ├── views.py
│   └── urls.py
├── logistics/
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
__
--- END ---