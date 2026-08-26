import azure.functions as func
import datetime
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from azure.servicebus import ServiceBusClient, ServiceBusMessage

app = func.FunctionApp()

STATUS_DIR = Path(os.environ.get("HOME", "/tmp")) / "data" / "document-status"
SERVICE_BUS_QUEUE = "document-processing"


@app.blob_trigger(arg_name="blob", path="uploads/{name}", connection="AzureWebJobsStorage")
def on_document_uploaded(blob: func.InputStream):
    document_id = str(uuid4())
    blob_uri = f"https://{os.environ['STORAGE_ACCOUNT_NAME']}.blob.core.windows.net/{blob.name}"

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

    connection_string = os.environ["SERVICE_BUS_CONNECTION_STRING"]
    message_body = json.dumps({"document_id": document_id, "blob_uri": blob_uri})

    with ServiceBusClient.from_connection_string(connection_string) as client:
        with client.get_queue_sender(SERVICE_BUS_QUEUE) as sender:
            sender.send_messages(ServiceBusMessage(message_body, message_id=document_id))

    logging.info("Queued message: document_id=%s blob_uri=%s", document_id, blob_uri)
