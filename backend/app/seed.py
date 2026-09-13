"""Database seeding."""
import os
from datetime import datetime, timedelta, timezone

from .core import db, hash_pw, logger


async def seed():
    await db.users.create_index("email", unique=True, name="uniq_email")
    await db.password_resets.create_index("expiresAt", expireAfterSeconds=0, name="ttl_reset")

    seed_users = [
        {"id": "u-admin", "name": "Sergio (Admin)", "email": "admin@ss-coffee.de", "role": "admin",
         "pw": os.environ["SEED_ADMIN_PASSWORD"]},
        {"id": "u-sales", "name": "Marco Vertrieb", "email": "vertrieb@ss-coffee.de", "role": "sales",
         "salesRepId": "u-sales", "pw": os.environ["SEED_SALES_PASSWORD"]},
        {"id": "u-customer", "name": "Ristorante Roma", "email": "kunde@ss-coffee.de", "role": "customer",
         "companyId": "c1", "pw": os.environ["SEED_CUSTOMER_PASSWORD"]},
    ]
    for u in seed_users:
        pw = u.pop("pw")
        doc = {**u, "hashed_password": hash_pw(pw), "createdAt": datetime.now(timezone.utc).isoformat()}
        await db.users.update_one({"email": u["email"]}, {"$setOnInsert": doc}, upsert=True)

    if await db.companies.count_documents({}) == 0:
        companies = [
            {"id": "c1", "name": "Ristorante Roma GmbH", "city": "Nürnberg", "email": "info@roma.de",
             "phone": "0911 123456", "vatId": "DE123456789", "assignedSalesRepId": "u-sales", "active": True,
             "monthlyKg": 48, "orderCycleDays": 14},
            {"id": "c2", "name": "Bar Milano GmbH", "city": "Fürth", "email": "ciao@milano.de",
             "phone": "0911 987654", "vatId": "DE987654321", "assignedSalesRepId": "u-admin", "active": True,
             "monthlyKg": 72, "orderCycleDays": 21},
            {"id": "c3", "name": "Eis Venezia", "city": "Ingolstadt", "email": "info@venezia.de",
             "phone": "0841 555123", "vatId": "DE555444333", "assignedSalesRepId": "u-sales", "active": True,
             "monthlyKg": 110, "orderCycleDays": 30},
            {"id": "c4", "name": "Caffè Torino", "city": "Erlangen", "email": "hallo@torino.de",
             "phone": "09131 44556", "vatId": "DE444555666", "assignedSalesRepId": "u-sales", "active": True,
             "monthlyKg": 35, "orderCycleDays": 14},
        ]
        await db.companies.insert_many(companies)

    if await db.products.count_documents({}) == 0:
        products = [
            {"id": "p1", "name": "Espresso Bar", "brand": "Gambilongo", "unit": "kg", "standardPrice": 16.90,
             "salesFloor": 15.90, "absoluteFloor": 14.90, "cost": 11.50, "active": True, "taxRate": 7, "stock": None,
             "discountTiers": [{"minQty": 50, "price": 16.20}, {"minQty": 100, "price": 15.50}]},
            {"id": "p2", "name": "Strong", "brand": "Caffè Aiello", "unit": "kg", "standardPrice": 18.90,
             "salesFloor": 17.90, "absoluteFloor": 16.90, "cost": 15.35, "active": True, "taxRate": 7, "stock": None,
             "discountTiers": [{"minQty": 50, "price": 18.20}, {"minQty": 100, "price": 17.50}]},
            {"id": "p3", "name": "Crema Mousse", "brand": "S&S", "unit": "Stk.", "standardPrice": 12.90,
             "salesFloor": 11.90, "absoluteFloor": 10.90, "cost": 7.40, "active": True, "taxRate": 7, "stock": None},
            {"id": "p4", "name": "Decaf Gold", "brand": "Caffè Aiello", "unit": "kg", "standardPrice": 21.50,
             "salesFloor": 19.90, "absoluteFloor": 18.50, "cost": 14.20, "active": True, "taxRate": 7, "stock": None},
        ]
        await db.products.insert_many(products)

    if await db.customer_prices.count_documents({}) == 0:
        prices = [
            {"companyId": "c1", "productId": "p1", "price": 15.90},
            {"companyId": "c1", "productId": "p2", "price": 17.90},
            {"companyId": "c2", "productId": "p2", "price": 18.20},
            {"companyId": "c3", "productId": "p1", "price": 15.50},
            {"companyId": "c4", "productId": "p1", "price": 16.20},
        ]
        await db.customer_prices.insert_many(prices)

    if await db.offers.count_documents({}) == 0:
        await db.offers.insert_many([
            {"id": "A-2026-0187", "companyId": "c1", "createdBy": "u-sales", "status": "Freigabe nötig",
             "items": [{"productId": "p1", "qty": 80, "price": 15.50}], "reason": "Strategischer Kunde, 80 kg/Monat",
             "termMonths": 48, "createdAt": "2026-06-08T09:00:00"},
            {"id": "A-2026-0181", "companyId": "c3", "createdBy": "u-sales", "status": "Freigegeben",
             "items": [{"productId": "p1", "qty": 110, "price": 15.50}], "reason": "",
             "termMonths": 36, "createdAt": "2026-05-20T09:00:00"},
        ])

    if await db.orders.count_documents({}) == 0:
        now = datetime.now(timezone.utc)
        cust_price = {("c1", "p1"): 15.90, ("c1", "p2"): 17.90, ("c2", "p2"): 18.20,
                      ("c3", "p1"): 15.50, ("c4", "p1"): 16.20}
        std = {"p1": 16.90, "p2": 18.90, "p3": 12.90, "p4": 21.50}
        base = {"c1": ("p1", 18), "c2": ("p2", 24), "c3": ("p1", 38), "c4": ("p1", 12)}
        orders = []
        counter = 1
        for cid, (pid, qty) in base.items():
            for i in range(6, 0, -1):
                dt = now - timedelta(days=i * 28 + (counter % 5))
                price = cust_price.get((cid, pid), std[pid])
                q = qty + (i % 3) * 3
                orders.append({
                    "id": f"B-2026-{counter:05d}", "companyId": cid, "createdBy": "u-sales", "status": "Abgeschlossen",
                    "items": [{"productId": pid, "qty": q, "price": price}],
                    "createdAt": dt.isoformat(),
                })
                counter += 1
        orders.append({
            "id": "B-2026-00987", "companyId": "c1", "createdBy": "u-customer", "status": "Neu",
            "items": [{"productId": "p1", "qty": 18, "price": 15.90}],
            "createdAt": (now - timedelta(days=2)).isoformat(),
        })
        await db.orders.insert_many(orders)

    if await db.contracts.count_documents({}) == 0:
        await db.contracts.insert_many([
            {"id": "S&S-2026-0187", "companyId": "c1", "productId": "p1", "start": "2026-01-01", "termMonths": 48,
             "minQtyMonth": 36, "price": 15.90, "machine": "BFC Lira", "machineRate": 129, "serviceRate": 24.90},
            {"id": "S&S-2026-0142", "companyId": "c3", "productId": "p1", "start": "2025-09-01", "termMonths": 36,
             "minQtyMonth": 90, "price": 15.50, "machine": "La Cimbali M26", "machineRate": 159, "serviceRate": 29.90},
        ])

    if await db.invoices.count_documents({}) == 0:
        await db.invoices.insert_many([
            {"id": "RE-2026-0987", "companyId": "c1", "date": "2026-06-03", "amount": 683.20, "status": "Bezahlt"},
            {"id": "RE-2026-0921", "companyId": "c1", "date": "2026-05-15", "amount": 1143.00, "status": "Offen"},
            {"id": "RE-2026-0850", "companyId": "c3", "date": "2026-05-28", "amount": 1705.00, "status": "Überfällig"},
            {"id": "RE-2026-0870", "companyId": "c2", "date": "2026-06-01", "amount": 872.40, "status": "Offen"},
        ])

    await db.offers.create_index("id", unique=True, name="uniq_offer_id")
    await db.orders.create_index("id", unique=True, name="uniq_order_id")
    await db.products.create_index("id", unique=True, name="uniq_product_id")
    if await db.counters.find_one({"_id": "offer"}) is None:
        await db.counters.insert_one({"_id": "offer", "seq": 200})
    if await db.counters.find_one({"_id": "order"}) is None:
        await db.counters.insert_one({"_id": "order", "seq": 1000})
    if await db.counters.find_one({"_id": "product"}) is None:
        prod_ids = [int(p["id"][1:]) for p in await db.products.find().to_list(1000) if p.get("id", "").startswith("p") and p["id"][1:].isdigit()]
        await db.counters.insert_one({"_id": "product", "seq": max(prod_ids) if prod_ids else 0})
    if await db.counters.find_one({"_id": "invoice"}) is None:
        await db.counters.insert_one({"_id": "invoice", "seq": 1000})

    logger.info("Seeding complete")
