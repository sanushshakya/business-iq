# Makefile for the iq project

# Default target
all: help

.PHONY: help install run migrate test shell docker-up docker-down docker-logs

# Help target to list available commands
help:
	@echo "Available targets:"
	@echo "  install      - Install dependencies"
	@echo "  run          - Run the application locally"
	@echo "  migrate      - Apply database migrations"
	@echo "  test         - Run tests"
	@echo "  shell        - Open a Django shell"
	@echo "  docker-up    - Start Docker container"
	@echo "  docker-down  - Stop Docker container"
	@echo "  docker-logs  - View Docker container logs"

# Install dependencies
install:
	pip install -r requirements.txt

# Run the application locally
run: install
	django-admin runserver

# Apply database migrations
migrate: install
	django-admin migrate

# Run tests
test: install
	django-admin test

# Open a Django shell
shell: install
	django-admin shell

# Start Docker container
docker-up:
	docker-compose up -d --build

# Stop Docker container
docker-down:
	docker-compose down

# View Docker container logs
docker-logs:
	docker-compose logs -f