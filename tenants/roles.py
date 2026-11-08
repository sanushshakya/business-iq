# tenants/roles.py

"""
Roles for operators: people who sign in with an 8-digit code and work in one area of the app.

Staff and other regular users have no role and see everything their permissions allow. What each role may
call is in ``authentication/role_access.py``; which screens it sees is in the frontend's ``roles.js``.
"""

TILL = 'till'
DEMAND = 'demand'
FREIGHT = 'freight'
SUPPLIER = 'supplier'
STOCK = 'stock'
DRIVER = 'driver'  # delivery runs (G44): their own runs and proof of delivery

ROLE_CHOICES = [
    (TILL, 'Till operator'),
    (DEMAND, 'Demand planner'),
    (FREIGHT, 'Freight manager'),
    (SUPPLIER, 'Supplier manager'),
    (STOCK, 'Stock controller'),
    (DRIVER, 'Delivery driver'),
]
ROLE_LABELS = dict(ROLE_CHOICES)
