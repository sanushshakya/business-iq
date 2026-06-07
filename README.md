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
  db:
    image: postgres:16-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: iqdb
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password

volumes:
  postgres_data:
```

This `docker-compose.yml` file defines a single service for the PostgreSQL database using the official `postgres:16-alpine` image. It uses a named volume `postgres_data` to persist data across container restarts.

### Docker Compose Installation Steps

To build and run the Docker Compose configuration:

1. **Build and Start Services**
   ```sh
   docker-compose up --build -d
   ```

2. **Verify Running Containers**
   ```sh
   docker-compose ps
   ```

3. **Access the Application**
   Open your web browser and navigate to `http://127.0.0.1:8000/`.
```

This README updates the existing content by adding a section dedicated to Docker configuration and explaining how to build and run the Docker container.