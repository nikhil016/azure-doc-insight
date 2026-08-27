import azure.functions as func
import datetime
import json
import logging
import os
from uuid import uuid4

from azure.cosmos import CosmosClient
from azure.servicebus import ServiceBusClient, ServiceBusMessage

app = func.FunctionApp()

SERVICE_BUS_QUEUE = "document-processing"
COSMOS_DATABASE = "ai200-doc-insight"
COSMOS_CONTAINER = "documents"


def get_documents_container():
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], os.environ["COSMOS_KEY"])
    return client.get_database_client(COSMOS_DATABASE).get_container_client(COSMOS_CONTAINER)


@app.blob_trigger(arg_name="blob", path="uploads/{name}", connection="AzureWebJobsStorage")
def on_document_uploaded(blob: func.InputStream):
    document_id = str(uuid4())
    blob_uri = f"https://{os.environ['STORAGE_ACCOUNT_NAME']}.blob.core.windows.net/{blob.name}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    record = {
        "id": document_id,
        "blob_name": blob.name,
        "blob_uri": blob_uri,
        "size_bytes": blob.length,
        "status": "uploaded",
        "created_at": now,
        "updated_at": now,
    }

    get_documents_container().upsert_item(record)

    logging.info(
        "Blob read and status recorded: name=%s size=%s document_id=%s",
        blob.name,
        blob.length,
        document_id,
    )

    connection_string = os.environ["SERVICE_BUS_CONNECTION_STRING"]
    message_body = json.dumps({"document_id": document_id, "blob_uri": blob_uri})

    with ServiceBusClient.from_connection_string(connection_string) as client:
        with client.get_queue_sender(SERVICE_BUS_QUEUE) as sender:
            sender.send_messages(ServiceBusMessage(message_body, message_id=document_id))

    logging.info("Queued message: document_id=%s blob_uri=%s", document_id, blob_uri)
