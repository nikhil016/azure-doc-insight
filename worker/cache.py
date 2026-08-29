import hashlib
import json
import os

from redis.cluster import RedisCluster
from azure.identity import DefaultAzureCredential

REDIS_SCOPE = "https://redis.azure.com/.default"
_credential = DefaultAzureCredential()


def _get_principal_object_id():
    """The Entra ID username for Redis auth must be the caller's object ID,
    not a UPN or client ID."""
    return os.environ["REDIS_AUTH_OBJECT_ID"]


def get_redis_client():
    token = _credential.get_token(REDIS_SCOPE)
    # Azure Managed Redis runs OSS Cluster mode: keys are sharded across
    # slots, so a cluster-aware client is required to follow MOVED redirects.
    # Cluster node discovery hands back per-node IPs whose TLS certs are only
    # issued for the cluster's DNS hostname, so hostname verification must be
    # skipped for those internal node connections (TLS encryption stays on;
    # only the hostname-vs-IP certificate check is relaxed) -- this is
    # Microsoft's own documented workaround for Azure Managed Redis clients.
    return RedisCluster(
        host=os.environ["REDIS_HOST"],
        port=int(os.environ.get("REDIS_PORT", "10000")),
        ssl=True,
        ssl_cert_reqs=None,
        username=_get_principal_object_id(),
        password=token.token,
        decode_responses=True,
    )


def question_hash(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:16]


def cache_key(user_id: str, question: str) -> str:
    return f"chat:{user_id}:{question_hash(question)}"


def get_cached_answer(client, user_id: str, question: str):
    raw = client.get(cache_key(user_id, question))
    return json.loads(raw) if raw else None


def set_cached_answer(client, user_id: str, question: str, answer: dict, ttl_seconds: int = 300):
    client.set(cache_key(user_id, question), json.dumps(answer), ex=ttl_seconds)


def invalidate_document(client, document_id: str):
    """Invalidate all cached answers that cited this document. Since chat
    keys are hashed by question (not document_id), we track a reverse index
    per document so we know which cache keys to drop when the source
    document changes."""
    index_key = f"doc-cache-index:{document_id}"
    cache_keys = client.smembers(index_key)
    # OSS Cluster mode shards keys across slots by hash, so a single
    # multi-key DEL fails with CROSSSLOT unless every key shares a slot.
    # Delete one at a time instead.
    for key in cache_keys:
        client.delete(key)
    client.delete(index_key)
    return len(cache_keys)


def track_cache_key_for_document(client, document_id: str, key: str):
    client.sadd(f"doc-cache-index:{document_id}", key)
