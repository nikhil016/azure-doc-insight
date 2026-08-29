# azure-doc-insight

AI-200 hands-on practice project: a document ingestion and insight pipeline built incrementally across Azure compute, serverless, messaging, data, security, and monitoring services.

## Structure

- `api/` — containerized FastAPI document API (Docker, Azure Container Apps)
- `function-app/` — Python v2 Azure Function App with a blob-triggered ingestion handler (Blob Storage, Azure Functions)
- `worker/` — Service Bus consumer: downloads blobs, chunks/embeds PDFs into pgvector, tracks status in Cosmos DB, caches Q&A in Redis (Service Bus, Cosmos DB, PostgreSQL/pgvector, Azure Managed Redis, Key Vault, managed identity)
- `ui/` — Streamlit dashboard over the whole pipeline: upload documents, watch status live, ask questions, see cache hit/miss, invalidate cache. Run with `ui/run.sh`.

## Roadmap

1. Containerized API + Container Apps deploy
2. Blob Storage + Azure Functions
3. Event Grid + Service Bus
4. Cosmos DB + PostgreSQL/pgvector
5. Redis caching + Key Vault/Entra ID/RBAC
6. Application Insights + monitoring
7. Full integrated pipeline
