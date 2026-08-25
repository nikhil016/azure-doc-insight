from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(
    title="AI-200 Document API",
    version="0.1.0",
)


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
    return record


@app.get("/documents/{document_id}")
def get_document(document_id: str):
    document = documents.get(document_id)

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return document