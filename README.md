## Docker Configuration

The `Dockerfile` for the `iq` project is designed to use the `python:3.12-slim` base image, which provides a lightweight Python environment. The working directory within the container is set to `/app`.

### Installation Steps

To build and run the Docker container:

1. **Build the Docker Image**
   ```sh
   docker build -t iq-app .
   ```

2. **Run the Docker Container**
   ```sh
   docker run -d -p 8000:8000 --name iq-container iq-app
   ```

3. **Access the Application**
   Open your web browser and navigate to `http://127.0.0.1:8000/`.

### Dockerfile Explanation

- **Base Image**: Uses `python:3.12-slim` for a lightweight Python environment.
- **Working Directory**: Sets `/app` as the working directory within the container.
- **Copy Files**: Copies all files from the current directory into the `/app` directory inside the container.
- **Install Dependencies**: Installs packages listed in `requirements/base.txt`.
- **Expose Port**: Makes port 8000 available outside the container.
- **Environment Variable**: Sets an environment variable for demonstration purposes.
- **CMD**: Specifies the command to run when starting the container, which is `gunicorn` serving the application.

### Docker Compose

To use Docker Compose with this project, create a `docker-compose.yml` file in your project root:

```yaml
version: '3.8'

services:
  backend:
    build: .
    env_file: .env
    volumes:
      - ./iq:/app
    depends_on:
      - db
      - redis
    ports:
      - "8000:8000"
    environment:
      - DJANGO_SETTINGS_MODULE=config.settings.production

  celery-beat:
    build: .
    env_file: .env
    volumes:
      - ./iq:/app
    command: celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    depends_on:
      - backend
      - db
      - redis

  celery:
    build: .
    env_file: .env
    volumes:
      - ./iq:/app
    command: celery -A config worker --loglevel=info
    depends_on:
      - backend
      - db
      - redis

  db:
    image: postgres:16-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: iqdb
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"

volumes:
  postgres_data:
  redis_data:
```

This `docker-compose.yml` file defines four services: the backend using the current directory as its build context, which should contain a Dockerfile, and three supporting services: PostgreSQL, Redis, and Celery. The backend service depends on both the db and redis services to ensure they are up before starting, and the Celery service depends on all three services.

### Docker Compose Installation Steps

To build and run the Docker Compose configuration:

1. **Build and Start Services**
   ```sh
   docker-compose up --build
   ```

2. **Access the Application**
   Open your web browser and navigate to `http://127.0.0.1:8000/`.

### Additional Targets for Makefile

To simplify common tasks, you can add the following targets to your `Makefile`:

```makefile
# Install dependencies
install:
    pip install -r requirements/base.txt

# Run the application
run:
    gunicorn config.wsgi:application --bind 0.0.0.0:8000

# Migrate database
migrate:
    python manage.py migrate

# Test the application
test:
    python manage.py test

# Open a Python shell in the container
shell:
    docker exec -it iq-container /bin/bash

# Start Docker Compose services
docker-up:
    docker-compose up --build

# Stop Docker Compose services
docker-down:
    docker-compose down

# View Docker logs
docker-logs:
    docker-compose logs -f
```

### Update the README to Include Makefile Targets

Update your `README.md` to include information about the new targets:

## Additional Targets for Makefile

To simplify common tasks, you can use the following commands from a `Makefile` located at the root of your project:

- **Install Dependencies**
  ```sh
  make install
  ```

- **Run the Application**
  ```sh
  make run
  ```

- **Migrate Database**
  ```sh
  make migrate
  ```

- **Test the Application**
  ```sh
  make test
  ```

- **Open a Python Shell in the Container**
  ```sh
  make shell
  ```

- **Start Docker Compose Services**
  ```sh
  make docker-up
  ```

- **Stop Docker Compose Services**
  ```sh
  make docker-down
  ```

- **View Docker Logs**
  ```sh
  make docker-logs
  ```