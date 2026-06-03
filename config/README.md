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

4. **Apply Migrations**
   ```sh
   python manage.py migrate
   ```

5. **Create a Superuser**
   ```sh
   python manage.py createsuperuser
   Follow the prompts to create a superuser account.
   ```

6. **Run the Development Server**
   ```sh
   python manage.py runserver
   ```

   The development server will start at `http://127.0.0.1:8000/`.

### Directory Structure

```
iq/
├── config/
│   ├── README.md
│   ├── asgi.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── tenants/
│   ├── apps.py
│   ├── migrations/
│   ├── models.py
│   ├── tests.py
│   ├── views.py
│   └── admin.py
└── authentication/
    ├── apps.py
    ├── migrations/
    ├── models.py
    ├── tests.py
    └── views.py
```

### Key Files

- **config/settings.py**: Contains all the configuration settings for the Django project.
- **tenants/models.py**: Defines models for tenant-specific data.
- **authentication/models.py**: Defines user authentication-related models.
- **config/urls.py**: Maps URLs to views.

### Additional Notes

- Ensure that you have the correct permissions to read and write files in the project directory.
- For production environments, consider using a proper WSGI server (like Gunicorn) instead of the built-in development server.