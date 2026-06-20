## Inventory App Product Model and CRUD REST Endpoints

This update to the inventory app introduces a new `Product` model with fields for company foreign key, SKU code, name, category, unit, reorder threshold, target margin percentage, perishability status, and shelf life days. The app now includes full CRUD (Create, Read, Update, Delete) REST endpoints for managing products, inheriting from `TenantModelViewSet`. Additionally, filtering by category and search by name/sku are supported.

### Product Model

The `Product` model is designed to store detailed information about each product in the inventory. It includes fields such as:

- **company**: Foreign key linking to the company that owns the product.
- **sku_code**: Unique stock keeping unit code for the product.
- **name**: Name of the product.
- **category**: Category of the product (grains, dairy, produce, spices, beverages, other).
- **unit**: Unit in which the product is measured (kg, g, litre, unit).
- **reorder_threshold**: Minimum stock level to trigger a reorder.
- **target_margin_percent**: Target profit margin percentage for the product.
- **is_perishable**: Boolean indicating whether the product is perishable.
- **shelf_life_days**: Number of days before the product's shelf life expires.

### CRUD REST Endpoints

The app now includes the following REST endpoints for managing products:

1. **Create Product**
   - **URL**: `/api/products/`
   - **Method**: `POST`
   - **Request Body**:
     ```json
     {
       "company": 1,
       "sku_code": "SKU12345",
       "name": "Product Name",
       "category": "grains",
       "unit": "kg",
       "reorder_threshold": 10,
       "target_margin_percent": 5.0,
       "is_perishable": false,
       "shelf_life_days": 90
     }
     ```

2. **Read Products**
   - **URL**: `/api/products/`
   - **Method**: `GET`
   - **Query Parameters**:
     - `category`: Filter products by category.
     - `search`: Search products by name or SKU code.

3. **Update Product**
   - **URL**: `/api/products/{id}/`
   - **Method**: `PUT`
   - **Request Body**:
     ```json
     {
       "name": "Updated Product Name",
       "category": "dairy"
     }
     ```

4. **Delete Product**
   - **URL**: `/api/products/{id}/`
   - **Method**: `DELETE`

### Additional Features

- **Filtering by Category**: Products can be filtered by category using the `category` query parameter.
- **Search by Name/Sku**: Products can be searched by name or SKU code using the `search` query parameter.

These enhancements will greatly improve the functionality and usability of the inventory app, allowing for more efficient management of product data.

---

## Multi-stage Dockerfile for Django App

This section describes the use of a multi-stage Docker build process to create an efficient production-ready image for the Django application. The builder stage installs dependencies into a virtual environment (venv), while the final stage uses `python:3.12-slim` and copies the venv, runs `collectstatic`, and starts the app with Gunicorn.

### Multi-stage Dockerfile

```dockerfile
# Builder Stage
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends gcc

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Final Stage
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

COPY --from=builder /app/venv /app/venv
COPY config/settings/production.py .
COPY manage.py .

RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc

RUN python manage.py collectstatic --noinput

CMD ["gunicorn", "supplyiq.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
```

This Dockerfile ensures that only the necessary files and dependencies are included in the final image, reducing its size and improving performance.

### docker-compose.prod.yml

The `docker-compose.prod.yml` file is used to define services for production use with all secrets passed as environment variables.

```yaml
version: '3.8'

services:
  web:
    build:
      context: .
      target: builder
    image: inventory_app_prod
    command: gunicorn supplyiq.wsgi:application --bind 0.0.0.0:8000 --workers 4
    ports:
      - "8000:8000"
    volumes:
      - ./staticfiles:/app/staticfiles
      - ./media:/app/media
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - CELERY_BROKER_URL=${CELERY_BROKER_URL}
      - CELERY_RESULT_BACKEND=${CELERY_RESULT_BACKEND}

  redis:
    image: "redis:latest"
    ports:
      - "6379:6379"

  db:
    image: postgres:14
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - db_data:/var/lib/postgresql/data

volumes:
  db_data:
```

This configuration ensures that the production environment is set up correctly with all necessary dependencies and secrets passed as environment variables.

---

### README.md (Updated)

## Inventory App Product Model and CRUD REST Endpoints

This update to the inventory app introduces a new `Product` model with fields for company foreign key, SKU code, name, category, unit, reorder threshold, target margin percentage, perishability status, and shelf life days. The app now includes full CRUD (Create, Read, Update, Delete) REST endpoints for managing products, inheriting from `TenantModelViewSet`. Additionally, filtering by category and search by name/sku are supported.

### Product Model

The `Product` model is designed to store detailed information about each product in the inventory. It includes fields such as:

- **company**: Foreign key linking to the company that owns the product.
- **sku_code**: Unique stock keeping unit code for the product.
- **name**: Name of the product.
- **category**: Category of the product (grains, dairy, produce, spices, beverages, other).
- **unit**: Unit in which the product is measured (kg, g, litre, unit).
- **reorder_threshold**: Minimum stock level to trigger a reorder.
- **target_margin_percent**: Target profit margin percentage for the product.
- **is_perishable**: Boolean indicating whether the product is perishable.
- **shelf_life_days**: Number of days before the product's shelf life expires.

### CRUD REST Endpoints

The app now includes the following REST endpoints for managing products:

1. **Create Product**
   - **URL**: `/api/products/`
   - **Method**: `POST`
   - **Request Body**:
     ```json
     {
       "company": 1,
       "sku_code": "SKU12345",
       "name": "Product Name",
       "category": "grains",
       "unit": "kg",
       "reorder_threshold": 10,
       "target_margin_percent": 5.0,
       "is_perishable": false,
       "shelf_life_days": 90
     }
     ```

2. **Read Products**
   - **URL**: `/api/products/`
   - **Method**: `GET`
   - **Query Parameters**:
     - `category`: Filter products by category.
     - `search`: Search products by name or SKU code.

3. **Update Product**
   - **URL**: `/api/products/{id}/`
   - **Method**: `PUT`
   - **Request Body**:
     ```json
     {
       "name": "Updated Product Name",
       "category": "dairy"
     }
     ```

4. **Delete Product**
   - **URL**: `/api/products/{id}/`
   - **Method**: `DELETE`

### Additional Features

- **Filtering by Category**: Products can be filtered by category using the `category` query parameter.
- **Search by Name/Sku**: Products can be searched by name or SKU code using the `search` query parameter.

These enhancements will greatly improve the functionality and usability of the inventory app, allowing for more efficient management of product data.

### Deployment

To deploy the application in production:

1. Build the Docker image:
   ```sh
   docker-compose -f docker-compose.prod.yml build
   ```

2. Run the services:
   ```sh
   docker-compose -f docker-compose.prod.yml up -d
   ```

3. Set environment variables (e.g., in a `.env` file):
   ```
   SECRET_KEY=your_secret_key_here
   DATABASE_URL=postgres://db_user:db_password@db/db_name
   REDIS_URL=redis://redis:6379/1
   CELERY_BROKER_URL=redis://redis:6379/1
   CELERY_RESULT_BACKEND=redis://redis:6379/2
   ```

4. Apply migrations:
   ```sh
   docker-compose -f docker-compose.prod.yml run --rm web python manage.py migrate
   ```

5. Collect static files:
   ```sh
   docker-compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput
   ```

6. Start the workers (if using Celery):
   ```sh
   docker-compose -f docker-compose.prod.yml up -d worker
   ```

This setup ensures a scalable and secure production environment for your Django application.

---