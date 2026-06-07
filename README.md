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

### Environment Variables

To configure the application, create a `.env` file in the root of your project with the following environment variables:

```sh
DJANGO_SECRET_KEY=your_secret_key_here
DATABASE_URL=postgres://user:password@db/iqdb
REDIS_URL=redis://redis:6379/0
```

These variables will be used to configure Django and other services within the application.