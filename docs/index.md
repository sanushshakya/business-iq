# docs/index.md

---

## Architecture Overview

### Project Setup

The project is built using Django, a high-level Python web framework that encourages rapid development and clean, pragmatic design. The application uses PostgreSQL as its primary database with SQLite for local development.

### Frameworks and Libraries

- **Django**: MVC architecture
- **Celery**: Task queue for background processing
- **Redis**: In-memory data structure store
- **JWT (JSON Web Tokens)**: Authentication method
- **Pytest**: Testing framework
- **Docker**: Containerization
- **Sphinx**: Documentation generator

### Key Modules

#### auth/dependencies.py
- Manages dependencies required for authentication processes.

#### auth/jwt_handler.py
- Handles the encoding and decoding of JWTs with an expiration time.

#### auth/password.py
- Provides utility methods to hash and verify passwords using bcrypt.

#### authentication/models.py
- Defines Django models related to authentication, specifically for Shopify store connections.

#### authentication/serializers.py
- Serializes data used in password reset confirmation views.

#### authentication/signals.py
- Handles signals for authentication-related events.

#### authentication/tests.py
- Contains unit tests for the authentication module.

#### celery_app/tasks.py
- Defines Celery tasks that process price change logs and update product information on Shopify.

#### common/exceptions.py
- Base custom exception class used across the application.

#### common/pagination.py
- Custom pagination classes used throughout the application.

#### common/serializers.py
- Common serializers used across different modules.

#### common/services/cost_calculation_service.py
- Service class for calculating costs associated with products or transactions.

#### common/services/hijri_calendar_service.py
- Service layer for interacting with the AlAdhan.com Hijri calendar API to fetch events.

#### common/services/hmrctariff_service.py
- Class to fetch import duty rates from HMRC API.

#### common/services/price_recommendation_service.py
- Service class to compute recommended retail prices based on landed cost and margin.

#### common/services/shopify_service.py
- Service class for interacting with the Shopify Admin REST API.

#### common/services/verification_token_service.py
- Service class for handling verification tokens.

#### common/tests/test_demand_alert.py
- Unit tests for demand alert functionality.

#### common/tests/test_hijri_calendar_service.py
- Unit tests for the HijriCalendarService class.

#### common/tests/test_hmrctariff_service.py
- Unit tests for the HMRC Tariff Service.

#### common/tests/test_price_recommendation_service.py
- Unit tests for the PriceRecommendationService class.

#### common/tests/test_scan_demand_alert.py
- Unit tests for demand alert scanning functionality.

#### common/tests.py
- General testing utilities and fixtures.

#### common/urls.py
- Common URL patterns used across different modules.

#### common/utils/redis_cache.py
- Module for caching API responses using Redis.

### Dependencies

- Django, asgiref, pytz, sqlparse, psycopg2-binary, django-allauth, django-tenants, pytest, black, flake8, sphinx, djangorestframework

---

This documentation provides an overview of the project's architecture, key modules, and dependencies. It is intended to help developers understand and navigate the codebase effectively.