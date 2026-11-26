import time

import psycopg
import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from config import settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()

REQUESTS = Counter(
    "http_requests_total",
    "Всего HTTP-запросов",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Время ответа в секундах",
    ["method", "path"],
)
DB_ERRORS = Counter("db_errors_total", "Ошибки подключения к базе")

app = FastAPI()

@app.middleware("http")
async  def observe_requests(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start_time

    REQUESTS.labels(method=request.method, path=request.url.path, status=response.status_code,).inc()
    LATENCY.labels(method=request.method, path=request.url.path).observe(duration)

    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
    )
    return response


@app.get("/")
def read_root():
    return {"message": settings.greeting}


@app.get("/health")
def liveness():
    return {"status": "alive"}


@app.get("/ready")
def readiness():
    try:
        with psycopg.connect(settings.database_url, connect_timeout=2, options="-c statement_timeout=1000",) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        DB_ERRORS.inc()
        logger.warning("readiness_failed", reason="database_unavailable")
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready"}


class Alert(BaseModel):
    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = {}

class AlertmanagerWebhook(BaseModel):
    status: str
    alerts: list[Alert]

@app.post("/alerts/webhook")
def alerts_webhook(payload: AlertmanagerWebhook):
    for alert in payload.alerts:
        logger.warning(
            "alert_received",
            alertname=alert.labels.get("alertname"),
            severity=alert.labels.get("severity"),
            status=alert.status,
            summary=alert.annotations.get("summary"),
        )
    return {"received": len(payload.alerts)}

@app.get("/stats")
def stats():
    try:
        with psycopg.connect(
            settings.database_url,
            connect_timeout=2,
            options="-c statement_timeout=1000",
        ) as conn:
            row = conn.execute("SELECT count(*) FROM pg_stat_activity").fetchone()
        return {"active_connections": row[0], "source": "database"}
    except psycopg.Error:
        logger.warning("stats_degraded", reason="database_unavailable")
        return {"active_connections": None, "source": "fallback"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

def call_with_retry(func, attempts=3, base_delay=0.2):
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except psycopg.Error:
            if attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning("retry", attempt=attempt, delay=delay)
            time.sleep(delay)