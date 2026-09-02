import argparse
import logging
import os

import psycopg2

from ingest import get_openai_client, get_pg_connection

logger = logging.getLogger("worker")


def semantic_search(question: str, document_id: str = None, top_k: int = 3):
    client = get_openai_client()
    response = client.embeddings.create(model="text-embedding-3-small", input=[question])
    logger.info("Query embedding token usage: total_tokens=%d", response.usage.total_tokens)
    query_embedding = response.data[0].embedding

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            if document_id:
                cur.execute(
                    """
                    SELECT document_id, page, chunk_index, content,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM document_chunks
                    WHERE document_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_embedding, document_id, query_embedding, top_k),
                )
            else:
                cur.execute(
                    """
                    SELECT document_id, page, chunk_index, content,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM document_chunks
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (query_embedding, query_embedding, top_k),
                )
            return cur.fetchall()
    finally:
        conn.close()


def keyword_search(question: str, document_id: str = None, top_k: int = 3):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            terms = " | ".join(question.split())
            if document_id:
                cur.execute(
                    """
                    SELECT document_id, page, chunk_index, content,
                           ts_rank(to_tsvector('english', content), to_tsquery('english', %s)) AS rank
                    FROM document_chunks
                    WHERE document_id = %s AND to_tsvector('english', content) @@ to_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s
                    """,
                    (terms, document_id, terms, top_k),
                )
            else:
                cur.execute(
                    """
                    SELECT document_id, page, chunk_index, content,
                           ts_rank(to_tsvector('english', content), to_tsquery('english', %s)) AS rank
                    FROM document_chunks
                    WHERE to_tsvector('english', content) @@ to_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s
                    """,
                    (terms, terms, top_k),
                )
            return cur.fetchall()
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--keyword", action="store_true", help="use keyword search instead of semantic")
    args = parser.parse_args()

    fn = keyword_search if args.keyword else semantic_search
    results = fn(args.question, document_id=args.document_id, top_k=args.top_k)

    for doc_id, page, chunk_index, content, score in results:
        print(f"[page {page}, chunk {chunk_index}, score {score:.4f}] {content[:150]!r}")
