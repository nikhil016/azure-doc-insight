import azure.functions as func
import datetime
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

app = func.FunctionApp()

STATUS_DIR = Path(os.environ.get("HOME", "/tmp")) / "data" / "document-status"


@app.blob_trigger(arg_name="blob", path="uploads/{name}", connection="AzureWebJobsStorage")
def on_document_uploaded(blob: func.InputStream):
    document_id = str(uuid4())

    record = {
        "id": document_id,
        "blob_name": blob.name,
        "size_bytes": blob.length,
        "status": "uploaded",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    status_file = STATUS_DIR / f"{document_id}.json"
    status_file.write_text(json.dumps(record))

    logging.info(
        "Blob read: name=%s size=%s document_id=%s status_file=%s",
        blob.name,
        blob.length,
        document_id,
        status_file,
    )
