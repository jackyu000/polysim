from fastapi import FastAPI
from sqlalchemy import text
from app.api.admin_markets import router as admin_markets_router
from app.core.db import SessionLocal
from app.api.auth import router as auth_router
from app.api.markets import router as markets_router
from app.api.orders import router as orders_router

app = FastAPI()
app.include_router(auth_router)
app.include_router(orders_router)
app.include_router(markets_router)
app.include_router(admin_markets_router)

@app.get("/")
def root():
    return {"status": "ok"}

@app.get("/db-check")
def db_check():
    db = SessionLocal()
    try:
        x = db.execute(text("SELECT 1")).scalar_one()
        return {"db": x}
    finally:
        db.close()
