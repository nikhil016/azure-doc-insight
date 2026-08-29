import azure.functions as func
import datetime
import json
import logging
import os
from uuid import uuid4

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.servicebus import ServiceBusClient, ServiceBusMessage

app = func.FunctionApp()

SERVICE_BUS_QUEUE = "document-processing"
COSMOS_DATABASE = "ai200-doc-insight"
COSMOS_CONTAINER = "documents"

_credential = None
_cosmos_key = None


def get_credential():
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def get_cosmos_key():
    global _cosmos_key
    if _cosmos_key is None:
        secret_client = SecretClient(vault_url=os.environ["KEY_VAULT_URL"], credential=get_credential())
        _cosmos_key = secret_client.get_secret("cosmos-primary-key").value
    return _cosmos_key


def get_documents_container():
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], get_cosmos_key())
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

    sb_namespace_fqdn = os.environ["SERVICE_BUS_NAMESPACE_FQDN"]
    message_body = json.dumps({"document_id": document_id, "blob_uri": blob_uri})

    with ServiceBusClient(fully_qualified_namespace=sb_namespace_fqdn, credential=get_credential()) as client:
        with client.get_queue_sender(SERVICE_BUS_QUEUE) as sender:
            sender.send_messages(ServiceBusMessage(message_body, message_id=document_id))

    logging.info("Queued message via managed identity: document_id=%s blob_uri=%s", document_id, blob_uri)
