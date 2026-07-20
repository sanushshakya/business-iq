# Makefile for Django project

.PHONY: clean runserver migrate test coverage

# Clean up build artifacts and cache
clean:
	rm -rf .venv/ build/ dist/
	find . -name "__pycache__" | xargs rm -r
	find . -name "*.pyc" | xargs rm

# Run the development server
runserver:
	python manage.py runserver

# Migrate database
migrate:
	python manage.py migrate

# Run tests
test:
	python manage.py test

# Generate coverage report
coverage:
	pytest --cov=.