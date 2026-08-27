import datetime
import json
import logging
import os
import sys
from urllib.parse import urlparse

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.servicebus import ServiceBusClient
from azure.storage.blob import BlobServiceClient

from ingest import ingest_document

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)

QUEUE_NAME = "document-processing"
COSMOS_DATABASE = "ai200-doc-insight"
COSMOS_CONTAINER = "documents"


def get_documents_container():
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], os.environ["COSMOS_KEY"])
    return client.get_database_client(COSMOS_DATABASE).get_container_client(COSMOS_CONTAINER)


def get_document(container, document_id):
    try:
        return container.read_item(item=document_id, partition_key=document_id)
    except CosmosResourceNotFoundError:
        return None


def write_status(container, document_id, status, extra=None):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing = get_document(container, document_id) or {"id": document_id, "created_at": now}
    existing.update(extra or {})
    existing["status"] = status
    existing["updated_at"] = now
    container.upsert_item(existing)


def download_blob(blob_uri, connection_string):
    parsed = urlparse(blob_uri)
    _, container, *blob_parts = parsed.path.split("/")
    blob_name = "/".join(blob_parts)
    blob_service = BlobServiceClient.from_connection_string(connection_string)
    blob_client = blob_service.get_blob_client(container=container, blob=blob_name)
    return blob_client.download_blob().readall()


def process_message(body, storage_connection_string, documents_container, simulate_crash_before_complete=False):
    document_id = body["document_id"]
    blob_uri = body["blob_uri"]

    existing = get_document(documents_container, document_id)
    if existing and existing.get("status") == "completed":
        logger.warning("Duplicate delivery detected, skipping reprocessing: document_id=%s", document_id)
        return "duplicate"

    write_status(documents_container, document_id, "processing", {"blob_uri": blob_uri})
    logger.info("Processing document_id=%s blob_uri=%s", document_id, blob_uri)

    content = download_blob(blob_uri, storage_connection_string)
    logger.info("Downloaded document_id=%s bytes=%d", document_id, len(content))

    if simulate_crash_before_complete:
        logger.error("Simulated crash before complete_message: document_id=%s", document_id)
        raise RuntimeError("simulated crash")

    blob_name = urlparse(blob_uri).path.split("/", 2)[-1]
    chunk_count = ingest_document(document_id, blob_name, content)
    logger.info("Ingested document_id=%s chunks=%d", document_id, chunk_count)

    write_status(
        documents_container,
        document_id,
        "completed",
        {"blob_uri": blob_uri, "bytes": len(content), "chunk_count": chunk_count},
    )
    logger.info("Completed document_id=%s", document_id)
    return "completed"


def run(max_messages=None, simulate_crash_before_complete=False):
    sb_connection_string = os.environ["SERVICE_BUS_CONNECTION_STRING"]
    storage_connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    documents_container = get_documents_container()

    handled = 0
    with ServiceBusClient.from_connection_string(sb_connection_string) as client:
        with client.get_queue_receiver(QUEUE_NAME, max_wait_time=20) as receiver:
            for msg in receiver:
                try:
                    raw_body = b"".join(msg.body).decode("utf-8")
                    payload = json.loads(raw_body)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.error("Malformed message, dead-lettering: %s", exc)
                    receiver.dead_letter_message(msg, reason="MalformedJson", error_description=str(exc))
                    handled += 1
                    if max_messages and handled >= max_messages:
                        break
                    continue

                try:
                    result = process_message(
                        payload,
                        storage_connection_string,
                        documents_container,
                        simulate_crash_before_complete=simulate_crash_before_complete,
                    )
                    receiver.complete_message(msg)
                    logger.info("Message completed: document_id=%s result=%s", payload.get("document_id"), result)
                except Exception as exc:
                    logger.error(
                        "Processing failed, abandoning for retry: document_id=%s error=%s delivery_count=%s",
                        payload.get("document_id"),
                        exc,
                        msg.delivery_count,
                    )
                    receiver.abandon_message(msg)

                handled += 1
                if max_messages and handled >= max_messages:
                    break

    logger.info("Worker run complete. Messages handled: %d", handled)


if __name__ == "__main__":
    simulate_crash = "--simulate-crash" in sys.argv
    run(simulate_crash_before_complete=simulate_crash)
