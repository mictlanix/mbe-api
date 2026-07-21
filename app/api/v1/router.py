from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    auth,
    cash_drawers,
    customers,
    employees,
    exchange_rates,
    expenses,
    facilities,
    health,
    labels,
    payment_method_options,
    points_of_sale,
    price_lists,
    product_prices,
    products,
    sat_catalogs,
    suppliers,
    taxpayer_recipients,
    users,
    vehicle_operators,
    vehicles,
    warehouses,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix='/health', tags=['health'])
api_router.include_router(auth.router, prefix='/auth', tags=['auth'])
api_router.include_router(users.router, prefix='/users', tags=['users'])
api_router.include_router(products.router, prefix='/products', tags=['products'])
api_router.include_router(price_lists.router, prefix='/price-lists', tags=['price-lists'])
api_router.include_router(product_prices.router, prefix='/product-prices', tags=['product-prices'])
api_router.include_router(customers.router, prefix='/customers', tags=['customers'])
api_router.include_router(labels.router, prefix='/labels', tags=['labels'])
api_router.include_router(
    taxpayer_recipients.router, prefix='/taxpayer-recipients', tags=['taxpayer-recipients']
)
api_router.include_router(suppliers.router, prefix='/suppliers', tags=['suppliers'])
api_router.include_router(employees.router, prefix='/employees', tags=['employees'])
api_router.include_router(addresses.router, prefix='/addresses', tags=['addresses'])
api_router.include_router(facilities.router, prefix='/facilities', tags=['facilities'])
api_router.include_router(warehouses.router, prefix='/warehouses', tags=['warehouses'])
api_router.include_router(points_of_sale.router, prefix='/points-of-sale', tags=['points-of-sale'])
api_router.include_router(cash_drawers.router, prefix='/cash-drawers', tags=['cash-drawers'])
api_router.include_router(exchange_rates.router, prefix='/exchange-rates', tags=['exchange-rates'])
api_router.include_router(expenses.router, prefix='/expenses', tags=['expenses'])
api_router.include_router(
    payment_method_options.router,
    prefix='/payment-method-options',
    tags=['payment-method-options'],
)
api_router.include_router(vehicles.router, prefix='/vehicles', tags=['vehicles'])
api_router.include_router(
    vehicle_operators.router, prefix='/vehicle-operators', tags=['vehicle-operators']
)
api_router.include_router(sat_catalogs.router, prefix='/sat', tags=['sat-catalogs'])
