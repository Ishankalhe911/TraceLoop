import os
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from database import Base, engine, SessionLocal

# --- ALL ROUTERS ---
from routes.user_routes import router as user_router
from routes.device_routes import router as device_router
from routes.verification_routes import router as verification_router
from routes.ownership_routes import router as ownership_router
from routes.recycling_routes import router as recycling_router
from routes.dispute_routes import router as dispute_router
from routes.ledger_routes import router as ledger_router
from routes.admin_routes import router as admin_router
from routes.device_profile_routes import router as device_profile_router
from routes.manufacturer_routes import router as manufacturer_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TraceLoop")

# --- SUBSCRIBER DAEMON (background thread) ---
def _start_subscriber():
    """
    Runs the Algorand event subscriber in a daemon thread.
    Polls Algorand Indexer every 5s, mirrors ARC-28 events to PostgreSQL.
    Must not block the FastAPI startup.
    """
    try:
        from services.subscriber_service import SubscriberDaemon
        daemon = SubscriberDaemon()
        logger.info("Subscriber daemon starting...")
        daemon.start()
    except Exception as e:
        logger.error(f"Subscriber daemon crashed: {e}")

# --- LIFESPAN (startup / shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    logger.info("TraceLoop Pillar 2 starting up...")

    # 1. Create all DB tables (idempotent — safe to run every boot)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified.")

    # 2. Verify DB connection
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("Database connection OK.")
    except Exception as e:
        logger.error(f"Database connection FAILED: {e}")

    # 3. Start subscriber daemon in background thread
    # daemon=True ensures it dies with the main process
    subscriber_thread = threading.Thread(target=_start_subscriber, daemon=True)
    subscriber_thread.start()
    logger.info("Subscriber daemon thread launched.")

    yield  # App is running

    # SHUTDOWN
    logger.info("TraceLoop Pillar 2 shutting down.")

# --- APP INIT ---
app = FastAPI(
    title="Trace-Loop API",
    description=(
        "Blockchain lifecycle passport for used laptops. "
        "Pillar 2: FastAPI backend bridging Web2 (PostgreSQL) and Web3 (Algorand TestNet)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# --- CORS ---
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- REGISTER ROUTERS ---
# Auth & Identity
app.include_router(user_router)

# Core Lifecycle (Pillar 2 — chain-integrated)
app.include_router(device_router)
app.include_router(verification_router)
app.include_router(ownership_router)
app.include_router(recycling_router)
app.include_router(dispute_router)
app.include_router(ledger_router)
app.include_router(admin_router)

# Device Intelligence (Pillar 3)
app.include_router(device_profile_router)
app.include_router(manufacturer_router)

# --- HEALTH CHECK ---
@app.get("/", tags=["Health"])
def root():
    return {
        "status": "success",
        "code": 200,
        "data": {
            "service": "Trace-Loop Pillar 2",
            "version": "1.0.0",
            "network": "Algorand TestNet",
            "app_id": os.getenv("APP_ID", "770956001"),
            "docs": "/docs"
        },
        "error": None,
        "traceloop_version": "v1"
    }

@app.get("/health", tags=["Health"])
def health_check():
    """Liveness probe — checks DB connectivity."""
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "success",
        "code": 200,
        "data": {
            "api": "ok",
            "database": db_status,
            "subscriber": "running (daemon thread)"
        },
        "error": None,
        "traceloop_version": "v1"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "production") == "development"
    )
