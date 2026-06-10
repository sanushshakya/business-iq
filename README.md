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