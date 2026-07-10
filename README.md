# Project Overview

This project is a comprehensive inventory management system built with Django, React, and other relevant technologies. It allows users to manage products, companies, and user authentication through REST APIs.

## Local Dev Setup with Docker Compose (Step by Step)

1. **Install Docker and Docker Compose**: Ensure you have Docker and Docker Compose installed on your machine.
2. **Clone the Repository**: Clone this repository to your local machine.
   ```sh
   git clone https://github.com/your-repo/inventory-app.git
   cd inventory-app
   ```
3. **Build the Docker Images**: Run the following command to build the necessary Docker images.
   ```sh
   docker-compose up --build
   ```
4. **Migrate Database**: After the containers are running, apply migrations to set up the database schema.
   ```sh
   docker-compose run web python manage.py migrate
   ```
5. **Create Superuser**: Create a superuser to access the Django admin panel.
   ```sh
   docker-compose run web python manage.py createsuperuser
   ```
6. **Run Migrations and Seed Demo Data**: Run migrations and seed demo data using the provided scripts.
   ```sh
   docker-compose run web python manage.py loaddata initial_data.json
   ```
7. **Access the Application**: Open your web browser and navigate to `http://localhost:3000` to access the React frontend.

## Environment Variable Reference Table

| Variable Name          | Description                           | Example Value  |
|------------------------|---------------------------------------|----------------|
| DJANGO_SECRET_KEY        | Secret key for Django                 | mysecretkey    |
| DEBUG                    | Enable or disable debug mode            | true           |
| DATABASE_URL             | Database URL                          | postgresql://user:password@db:5432/mydatabase |
| REDIS_URL                | Redis URL                             | redis://redis:6379/1 |
| CELERY_BROKER_URL        | Celery broker URL                     | amqp://guest:@celery-broker:5672// |
| SHOPIFY_APP_KEY          | Shopify app key                       | yourappkey     |
| SHOPIFY_APP_SECRET       | Shopify app secret                    | yourappsecret  |

## Running Migrations and Seed Demo Data

1. **Apply Migrations**:
   ```sh
   docker-compose run web python manage.py migrate
   ```
2. **Seed Demo Data**:
   ```sh
   docker-compose run web python manage.py loaddata initial_data.json
   ```

## Running Tests

To run the tests, execute the following command:
```sh
docker-compose run web python manage.py test
```

## API Endpoint Summary Grouped by Module

### Authentication Module

1. **Register User**
   - **URL**: `/api/register/`
   - **Method**: `POST`
   - **Request Body**:
     ```json
     {
       "username": "user",
       "email": "user@example.com",
       "password": "secure_password"
     }
     ```

2. **Login User**
   - **URL**: `/api/login/`
   - **Method**: `POST`
   - **Request Body**:
     ```json

---

## New Feature: Add Logo to Company Model

To add a logo to the Company model, follow these steps:

1. **Update Company Model**:
   - Modify the `authentication/models.py` file to include an `ImageField` for the logo.
2. **Configure Media Storage**:
   - Update the `settings.py` file to configure media storage either locally or on S3 for production environments.
3. **Migrate Database**:
   - Run migrations to apply the changes to the database schema.

### Detailed Steps

1. **Update Company Model (`authentication/models.py`)**:
   ```python
   from django.db import models
   from django.core.validators import URLValidator
   from django.contrib.auth.models import AbstractUser

   class Company(models.Model):
       """
       Model representing a company.

       Fields:
       - name: CharField representing the name of the company.
       - logo: ImageField representing the logo of the company.
       """

       name = models.CharField(max_length=255, unique=True)
       logo = models.ImageField(upload_to='company_logos/', null=True, blank=True)

       def __str__(self):
           return self.name
   ```

2. **Configure Media Storage (`settings.py`)**:
   ```python
   import os

   if 'DJANGO_ENV' in os.environ and os.environ['DJANGO_ENV'] == 'production':
       DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
       AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
       AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
       AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME')
   else:
       DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
       MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
   ```

3. **Migrate Database**:
   ```sh
   docker-compose run web python manage.py makemigrations authentication
   docker-compose run web python manage.py migrate
   ```

---

## API Endpoint Summary Grouped by Module

### Authentication Module

1. **Register User**
   - **URL**: `/api/register/`
   - **Method**: `POST`
   - **Request Body**:
     ```json
     {
       "username": "user",
       "email": "user@example.com",
       "password": "secure_password"
     }
     ```

2. **Login User**
   - **URL**: `/api/login/`
   - **Method**: `POST`
   - **Request Body**:
     ```json