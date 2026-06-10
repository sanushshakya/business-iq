# tests/test_feature.py

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from config.inventory.models import Product
from config.authentication.models import Company


@pytest.fixture(autouse=True)
def setup_company(db):
    """Create a company for testing."""
    return Company.objects.create(name="Test Company")


@pytest.fixture
def product_data():
    """Return product data dictionary."""
    return {
        "company": 1,
        "sku_code": "SKU001",
        "name": "Test Product",
        "category": "grains",
        "unit": "kg",
        "reorder_threshold": 10,
        "target_margin_percent": 5,
        "is_perishable": False,
        "shelf_life_days": None
    }


@pytest.fixture
def authenticated_client(company):
    """Return an authenticated client."""
    client = APIClient()
    response = client.post(reverse('authentication:register'), {
        'username': 'testuser',
        'password1': 'testpass',
        'password2': 'testpass',
        'email': 'test@example.com'
    })
    return client


@pytest.mark.django_db
def test_create_product(authenticated_client, product_data):
    """Test creating a new product."""
    response = authenticated_client.post(reverse('inventory:product-list'), product_data)
    assert response.status_code == 201
    assert Product.objects.count() == 1


@pytest.mark.django_db
def test_list_products(authenticated_client, setup_company, product_data):
    """Test listing products."""
    Product.objects.create(**product_data)
    response = authenticated_client.get(reverse('inventory:product-list'))
    assert response.status_code == 200
    assert len(response.data) == 1


@pytest.mark.django_db
def test_filter_products_by_category(authenticated_client, setup_company, product_data):
    """Test filtering products by category."""
    Product.objects.create(**{**product_data, "category": "dairy"})
    response = authenticated_client.get(reverse('inventory:product-list') + "?category=dairy")
    assert response.status_code == 200
    assert len(response.data) == 1


@pytest.mark.django_db
def test_search_products_by_name(authenticated_client, setup_company, product_data):
    """Test searching products by name."""
    Product.objects.create(**{**product_data, "name": "Another Test Product"})
    response = authenticated_client.get(reverse('inventory:product-list') + "?search=Test")
    assert response.status_code == 200
    assert len(response.data) == 1


@pytest.mark.django_db
def test_search_products_by_sku(authenticated_client, setup_company, product_data):
    """Test searching products by sku."""
    Product.objects.create(**{**product_data, "sku_code": "SKU002"})
    response = authenticated_client.get(reverse('inventory:product-list') + "?search=SKU")
    assert response.status_code == 200
    assert len(response.data) == 1


@pytest.mark.django_db
def test_update_product(authenticated_client, setup_company, product_data):
    """Test updating a product."""
    product = Product.objects.create(**product_data)
    update_data = {
        "sku_code": "SKU003",
        "name": "Updated Test Product",
        "category": "produce"
    }
    response = authenticated_client.put(reverse('inventory:product-detail', kwargs={'pk': product.pk}), update_data)
    assert response.status_code == 200
    updated_product = Product.objects.get(pk=product.pk)
    assert updated_product.sku_code == "SKU003"
    assert updated_product.name == "Updated Test Product"
    assert updated_product.category == "produce"


@pytest.mark.django_db
def test_delete_product(authenticated_client, setup_company, product_data):
    """Test deleting a product."""
    product = Product.objects.create(**product_data)
    response = authenticated_client.delete(reverse('inventory:product-detail', kwargs={'pk': product.pk}))
    assert response.status_code == 204
    assert Product.objects.count() == 0