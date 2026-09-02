import datetime
import json
import logging
import sys
from urllib.parse import urlparse

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from azure_clients import get_blob_service_client, get_documents_container, get_service_bus_client
from cache import get_redis_client, invalidate_document
from ingest import ingest_document
from telemetry import configure_monitoring, correlation_id_var

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s [correlation_id=%(correlation_id)s] %(message)s"
)
logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)
configure_monitoring(logger)

QUEUE_NAME = "document-processing"


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


def download_blob(blob_uri):
    parsed = urlparse(blob_uri)
    _, container, *blob_parts = parsed.path.split("/")
    blob_name = "/".join(blob_parts)
    blob_client = get_blob_service_client().get_blob_client(container=container, blob=blob_name)
    return blob_client.download_blob().readall()


def extract_correlation_id(msg, fallback):
    props = msg.application_properties or {}
    value = props.get("correlation_id", props.get(b"correlation_id", fallback))
    return value.decode("utf-8") if isinstance(value, bytes) else value


def process_message(body, documents_container, correlation_id, simulate_crash_before_complete=False):
    document_id = body["document_id"]
    blob_uri = body["blob_uri"]

    existing = get_document(documents_container, document_id)
    if existing and existing.get("status") == "completed":
        logger.warning("Duplicate delivery detected, skipping reprocessing: document_id=%s", document_id)
        return "duplicate"

    write_status(
        documents_container, document_id, "processing", {"blob_uri": blob_uri, "correlation_id": correlation_id}
    )
    logger.info("Processing document_id=%s blob_uri=%s", document_id, blob_uri)

    content = download_blob(blob_uri)
    logger.info("Downloaded document_id=%s bytes=%d", document_id, len(content))

    if simulate_crash_before_complete:
        logger.error("Simulated crash before complete_message: document_id=%s", document_id)
        raise RuntimeError("simulated crash")

    blob_name = urlparse(blob_uri).path.split("/", 2)[-1]
    chunk_count = ingest_document(document_id, blob_name, content)
    logger.info("Ingested document_id=%s chunks=%d", document_id, chunk_count)

    invalidated = invalidate_document(get_redis_client(), document_id)
    if invalidated:
        logger.info("Invalidated %d cached answers for document_id=%s", invalidated, document_id)

    write_status(
        documents_container,
        document_id,
        "completed",
        {"blob_uri": blob_uri, "bytes": len(content), "chunk_count": chunk_count, "correlation_id": correlation_id},
    )
    logger.info("Completed document_id=%s", document_id)
    return "completed"


def run(max_messages=None, simulate_crash_before_complete=False):
    documents_container = get_documents_container()

    handled = 0
    with get_service_bus_client() as client:
        with client.get_queue_receiver(QUEUE_NAME, max_wait_time=20) as receiver:
            for msg in receiver:
                correlation_id = extract_correlation_id(msg, fallback=str(msg.message_id))
                token = correlation_id_var.set(correlation_id)
                try:
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
                            documents_container,
                            correlation_id,
                            simulate_crash_before_complete=simulate_crash_before_complete,
                        )
                        receiver.complete_message(msg)
                        logger.info(
                            "Message completed: document_id=%s result=%s", payload.get("document_id"), result
                        )
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
                finally:
                    correlation_id_var.reset(token)

    logger.info("Worker run complete. Messages handled: %d", handled)


if __name__ == "__main__":
    simulate_crash = "--simulate-crash" in sys.argv
    run(simulate_crash_before_complete=simulate_crash)
