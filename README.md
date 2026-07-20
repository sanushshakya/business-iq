# README.md

## Project Overview

This project is a monolithic Django application built with a RESTful API style and utilizing PostgreSQL as the primary database. The application includes features such as user authentication, password reset functionality, and interactions with external services via APIs.

## Code Structure

### Key Modules

- **authentication/**: Contains models, views, and serializers related to user authentication.
  - `models.py`: Defines the `ShopifyStore` model for representing connections to Shopify stores.
  - `serializers.py`: Serializes data for password reset confirmation requests.
  - `views.py`: Views handling HTTP requests related to password reset confirmations.

- **common/**: Contains utility classes, services, and other shared components across the application.
  - `exceptions.py`: Defines a base custom exception class.
  - `pagination.py`: Custom pagination classes.
  - `serializers.py`: Additional serializers that can be reused across different parts of the app.
  - `services/**`: Service classes encapsulating business logic.
    - `cost_calculation_service.py`: Calculates costs associated with products or transactions.
    - `hijri_calendar_service.py`: Interacts with the AlAdhan.com Hijri calendar API.
    - `hmrctariff_service.py`: Fetches import duty rates from HMRC API.
    - `price_recommendation_service.py`: Computes recommended retail prices based on landed cost and margin.
    - `shopify_service.py`: Interacts with the Shopify Admin REST API.
    - `verification_token_service.py`: Handles verification tokens for various purposes.

- **celery_app/**: Contains Celery configuration and tasks.
  - `tasks.py`: Defines Celery tasks, such as processing price change logs to update product prices.

### Dependencies

The project depends on several libraries:
- Django
- asgiref
- pytz
- sqlparse
- psycopg2-binary
- django-allauth
- pytest
- black
- flake8
- sphinx
- djangorestframework

## Configuration and Setup

To set up the development environment, follow these steps:

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-repo/config.git
   cd config
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run migrations**:
   ```bash
   python manage.py migrate
   ```

5. **Create a superuser** (if needed):
   ```bash
   python manage.py createsuperuser
   ```

6. **Start the development server**:
   ```bash
   python manage.py runserver
   ```

## Testing

The project uses `pytest` for testing. To run tests, execute:

```bash
pytest
```

## Documentation

Documentation is generated using Sphinx. Build the documentation with:

```bash
sphinx-build -b html docs/source docs/build
```

Access the generated HTML documentation by opening `docs/build/index.html`.

## Code Conventions

- **Naming Style**: snake_case