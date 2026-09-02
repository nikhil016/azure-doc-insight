import io
import os

import psycopg2
from docx import Document
from openai import AzureOpenAI
from pypdf import PdfReader

from azure_clients import get_secret

EMBEDDING_DEPLOYMENT = "text-embedding-3-small"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def get_pg_connection():
    # Azure's firewall silently drops packets from disallowed IPs instead of
    # rejecting them, so a stale firewall rule hangs the TCP handshake
    # indefinitely without this -- fail fast with a clear error instead.
    return psycopg2.connect(
        host=os.environ["PG_HOST"],
        dbname=os.environ.get("PG_DATABASE", "postgres"),
        user=os.environ["PG_USER"],
        password=get_secret("pg-admin-password"),
        sslmode="require",
        connect_timeout=10,
    )


def get_openai_client():
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=get_secret("openai-api-key"),
        api_version="2024-06-01",
        timeout=20,
    )


def extract_pages(content: bytes, blob_name: str):
    """Return a list of (page_number, text) tuples. PDFs are parsed page by
    page; .docx files (a binary zip format, not text) are parsed paragraph
    by paragraph into a single page; anything else is treated as a single
    page of UTF-8 text."""
    name = blob_name.lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    if name.endswith(".docx"):
        document = Document(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return [(1, text)]
    return [(1, content.decode("utf-8", errors="replace"))]


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def embed_texts(client, texts):
    if not texts:
        return []
    response = client.embeddings.create(model=EMBEDDING_DEPLOYMENT, input=texts)
    return [item.embedding for item in response.data]


def ingest_document(document_id: str, blob_name: str, content: bytes):
    """Chunk the document per page, embed each chunk, and insert into pgvector.
    Returns the number of chunks inserted."""
    pages = extract_pages(content, blob_name)

    all_chunks = []  # list of (page_number, chunk_index, text)
    for page_number, page_text in pages:
        for chunk_index, chunk in enumerate(chunk_text(page_text)):
            all_chunks.append((page_number, chunk_index, chunk))

    if not all_chunks:
        return 0

    client = get_openai_client()
    embeddings = embed_texts(client, [c[2] for c in all_chunks])

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            for (page_number, chunk_index, chunk), embedding in zip(all_chunks, embeddings):
                cur.execute(
                    """
                    INSERT INTO document_chunks (document_id, blob_name, page, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (document_id, blob_name, page_number, chunk_index, chunk, embedding),
                )
        conn.commit()
    finally:
        conn.close()

    return len(all_chunks)
