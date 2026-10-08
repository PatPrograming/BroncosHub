# tests/conftest.py
# Shared test setup. pytest loads this automatically. Run tests with:  pytest
import pytest

from app import create_app
from app.extensions import db
from config import TestConfig


@pytest.fixture
def app():
    application = create_app(TestConfig)
    yield application
    with application.app_context():
        db.drop_all()
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()
