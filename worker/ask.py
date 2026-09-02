import argparse
import logging
import time
import uuid

from cache import (
    cache_key,
    get_cached_answer,
    get_redis_client,
    set_cached_answer,
    track_cache_key_for_document,
)
from ingest import get_openai_client
from search import semantic_search
from telemetry import configure_monitoring, correlation_id_var

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)
configure_monitoring(logger)

CHAT_DEPLOYMENT = "gpt-4.1-mini"


def generate_answer(question: str, results) -> str:
    """Synthesize an answer from retrieved chunks with a chat model, so
    questions that require reasoning across chunks (e.g. summing job dates
    into a total years of experience) get a real answer instead of the
    single nearest-matching raw text snippet."""
    context = "\n\n".join(
        f"[Source: page {page}]\n{content}" for _doc_id, page, _chunk_index, content, _score in results
    )

    client = get_openai_client()
    response = client.chat.completions.create(
        model=CHAT_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer the question using only the provided context. "
                    "If the context doesn't contain enough information, say so plainly. Be concise."
                ),
            },
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
        temperature=0,
        max_tokens=300,
    )
    logger.info("Chat completion token usage: total_tokens=%d", response.usage.total_tokens)
    return response.choices[0].message.content


def answer_question(redis_client, user_id: str, question: str, document_id: str = None, top_k: int = 3):
    correlation_id_var.set(str(uuid.uuid4()))
    cached = get_cached_answer(redis_client, user_id, question)
    if cached is not None:
        logger.info("Cache hit for question, user_id=%s", user_id)
        return {**cached, "cache_hit": True}
    logger.info("Cache miss for question, user_id=%s", user_id)

    results = semantic_search(question, document_id=document_id, top_k=top_k)
    if not results:
        return {"cache_hit": False, "answer": None, "sources": []}

    sources = [
        {"document_id": doc_id, "page": page, "chunk_index": chunk_index, "score": round(score, 4)}
        for doc_id, page, chunk_index, _content, score in results
    ]
    top_doc_id, top_page, _chunk_index, top_content, top_score = results[0]
    generated_answer = generate_answer(question, results)

    answer = {
        "question": question,
        "answer": generated_answer,
        "top_document_id": top_doc_id,
        "top_page": top_page,
        "top_content": top_content,
        "score": round(top_score, 4),
        "sources": sources,
    }

    set_cached_answer(redis_client, user_id, question, answer, ttl_seconds=300)
    key = cache_key(user_id, question)
    for source in sources:
        track_cache_key_for_document(redis_client, source["document_id"], key)

    return {**answer, "cache_hit": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--repeat", type=int, default=1, help="ask the same question N times to show cache hits")
    args = parser.parse_args()

    client = get_redis_client()
    for i in range(args.repeat):
        start = time.time()
        result = answer_question(client, args.user_id, args.question, document_id=args.document_id)
        elapsed_ms = (time.time() - start) * 1000
        print(f"[call {i + 1}] cache_hit={result['cache_hit']} elapsed={elapsed_ms:.1f}ms")
        print(f"  answer={result.get('answer')}")
        print(f"  top_page={result.get('top_page')} score={result.get('score')}")
        print(f"  content={result.get('top_content', '')[:120]!r}")
