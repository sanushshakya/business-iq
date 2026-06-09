"""
config/common/tasks.py

This module contains tasks that are common across all apps in the iq project.
"""

from invoke import task

@task
def clean(c):
    """
    Clean up the project by removing temporary files and directories.
    """
    c.run("rm -rf build dist .eggs")
    print("Cleanup complete.")

@task
def lint(c):
    """
    Lint Python files to ensure they conform to PEP 8 standards.
    """
    c.run("black --check .")
    print("Linting complete.")

@task
def test(c):
    """
    Run tests for the project using Django's testing framework.
    """
    c.run("python manage.py test")
    print("Tests completed.")