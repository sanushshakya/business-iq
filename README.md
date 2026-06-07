# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements/base.txt

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Define environment variable
ENV NAME World

# Run gunicorn command to serve the application
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
```

```markdown
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
```

This README updates the existing content by adding a section dedicated to Docker configuration and explaining how to build and run the Docker container.