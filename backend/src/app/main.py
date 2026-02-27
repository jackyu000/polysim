from fastapi import FastAPI
from sqlalchemy import text
from app.core.db import SessionLocal
from app.api.auth import router as auth_router

app = FastAPI()
app.include_router(auth_router)

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