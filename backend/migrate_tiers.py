import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")
db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def main():
    await db.products.update_one({"id": "p1"}, {"$set": {"discountTiers": [{"minQty": 50, "price": 16.20}, {"minQty": 100, "price": 15.50}]}})
    await db.products.update_one({"id": "p2"}, {"$set": {"discountTiers": [{"minQty": 50, "price": 18.20}, {"minQty": 100, "price": 17.50}]}})
    for p in await db.products.find({"discountTiers": {"$exists": False}}).to_list(1000):
        await db.products.update_one({"id": p["id"]}, {"$set": {"discountTiers": []}})
    print("tiers set:", [(p["id"], p.get("discountTiers")) for p in await db.products.find().to_list(100)])


asyncio.run(main())
