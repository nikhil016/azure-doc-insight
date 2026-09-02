import contextvars
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

correlation_id_var = contextvars.ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record):
        record.correlation_id = correlation_id_var.get()
        return True


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [correlation_id=%(correlation_id)s] %(message)s"
)
logging.getLogger().addFilter(CorrelationIdFilter())
logger = logging.getLogger("document-api")

if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(logger_name="document-api")

app = FastAPI(
    title="AI-200 Document API",
    version="0.1.0",
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-Id", str(uuid4()))
    correlation_id_var.set(correlation_id)
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


class DocumentCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = "application/pdf"


documents: dict[str, dict] = {}


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "document-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/documents", status_code=201)
def create_document(document: DocumentCreate):
    document_id = str(uuid4())

    record = {
        "id": document_id,
        "filename": document.filename,
        "content_type": document.content_type,
        "status": "uploaded",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    documents[document_id] = record
    logger.info("Document created: document_id=%s filename=%s", document_id, document.filename)
    return record


@app.get("/documents/{document_id}")
def get_document(document_id: str):
    document = documents.get(document_id)

    if document is None:
        logger.warning("Document not found: document_id=%s", document_id)
        raise HTTPException(status_code=404, detail="Document not found")

    logger.info("Document retrieved: document_id=%s", document_id)

    return document