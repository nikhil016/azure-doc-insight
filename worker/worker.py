import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from azure.servicebus import ServiceBusClient
from azure.storage.blob import BlobServiceClient

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)

QUEUE_NAME = "document-processing"
DATA_DIR = Path(__file__).parent / "data"
STATUS_DIR = DATA_DIR / "document-status"
PROCESSED_IDS_FILE = DATA_DIR / "processed_ids.json"


def load_processed_ids():
    if PROCESSED_IDS_FILE.exists():
        return set(json.loads(PROCESSED_IDS_FILE.read_text()))
    return set()


def mark_processed(document_id, processed_ids):
    processed_ids.add(document_id)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_IDS_FILE.write_text(json.dumps(sorted(processed_ids)))


def write_status(document_id, status, extra=None):
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    record = {"id": document_id, "status": status, **(extra or {})}
    (STATUS_DIR / f"{document_id}.json").write_text(json.dumps(record))


def download_blob(blob_uri, connection_string):
    parsed = urlparse(blob_uri)
    _, container, *blob_parts = parsed.path.split("/")
    blob_name = "/".join(blob_parts)
    blob_service = BlobServiceClient.from_connection_string(connection_string)
    blob_client = blob_service.get_blob_client(container=container, blob=blob_name)
    return blob_client.download_blob().readall()


def process_message(body, storage_connection_string, processed_ids, simulate_crash_before_complete=False):
    document_id = body["document_id"]
    blob_uri = body["blob_uri"]

    if document_id in processed_ids:
        logger.warning("Duplicate delivery detected, skipping reprocessing: document_id=%s", document_id)
        return "duplicate"

    write_status(document_id, "processing", {"blob_uri": blob_uri})
    logger.info("Processing document_id=%s blob_uri=%s", document_id, blob_uri)

    content = download_blob(blob_uri, storage_connection_string)
    logger.info("Downloaded document_id=%s bytes=%d", document_id, len(content))

    if simulate_crash_before_complete:
        logger.error("Simulated crash before complete_message: document_id=%s", document_id)
        raise RuntimeError("simulated crash")

    write_status(document_id, "completed", {"blob_uri": blob_uri, "bytes": len(content)})
    mark_processed(document_id, processed_ids)
    logger.info("Completed document_id=%s", document_id)
    return "completed"


def run(max_messages=None, simulate_crash_before_complete=False):
    sb_connection_string = os.environ["SERVICE_BUS_CONNECTION_STRING"]
    storage_connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    processed_ids = load_processed_ids()

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
                        processed_ids,
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
