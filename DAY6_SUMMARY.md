# Day 6 Summary: Application Insights + Distributed Tracing

## Status

✅ **Complete**: Correlation ID threading, Application Insights instrumentation, 8 KQL queries for troubleshooting.

## Changes made

### 1. Function App (`function-app/`)

**`function_app.py`**:
- Generate `correlation_id` UUID on blob trigger
- Store it in Cosmos status record
- Send it as Service Bus message `application_properties`
- Log with custom dimensions for App Insights

**`requirements.txt`**: No new deps (Functions host has built-in App Insights bridge)

### 2. FastAPI Service (`api/`)

**`main.py`**:
- Add HTTP middleware to extract/generate `X-Correlation-Id` header
- Set correlation ID in Python contextvar
- Apply logging filter so every log record carries it
- Wire Application Insights via `azure-monitor-opentelemetry` (if env var set)
- Add log lines to `/health`, POST `/documents`, GET `/documents/{id}` endpoints

**`requirements.txt`**: Add `azure-monitor-opentelemetry`

### 3. Worker Service (`worker/`)

**`telemetry.py` (new file)**:
- `CorrelationIdFilter`: logging filter that injects `correlation_id` into every record
- `configure_monitoring()`: sets up App Insights + filter
- `correlation_id_var`: ContextVar for thread-safe correlation ID propagation

**`worker.py`**:
- Extract `correlation_id` from Service Bus message `application_properties`
- Set it via `correlation_id_var.set()` per message
- Pass it to `process_message()` for Cosmos writes
- Store in Cosmos at each stage (processing → completed)
- All logs automatically carry it via filter

**`ask.py`**:
- Import and configure monitoring
- Set correlation ID per question (UUID)
- Log cache hit/miss for performance monitoring

**`search.py`**:
- Log embedding token usage from OpenAI API response
- Tied to correlation ID automatically

**`requirements.txt`**: Add `azure-monitor-opentelemetry`

### 4. Documentation

**`worker/DAY6_MONITORING.md`**: 8 runnable KQL queries:
1. Single correlation ID trace (full pipeline)
2. Failed requests (errors + exceptions)
3. Queue depth (dead-letters)
4. Slowest operations (latency analysis)
5. Token usage by hour (cost tracking)
6. Cache performance (hit rate)
7. API latency percentiles (SLA monitoring)
8. By-correlation error trace (debugging)

## How correlation ID flows

```
Blob Upload → Function App
  ├─ correlation_id = new UUID
  ├─ Cosmos: store in record
  └─ Service Bus message.application_properties["correlation_id"] = UUID
      │
      └─ Worker (Service Bus consumer)
         ├─ Extract correlation_id from message
         ├─ correlation_id_var.set(correlation_id)  ← all logs now carry it
         ├─ Cosmos: write in status record
         └─ Request to AI services / Postgres
            └─ All logs include correlation_id
```

For FastAPI:
```
HTTP Request (with/without X-Correlation-Id header)
  ├─ Middleware: extract or generate
  ├─ correlation_id_var.set()
  └─ All logs + response include it
```

## Environment variables

- **Function App**: `APPLICATIONINSIGHTS_CONNECTION_STRING` (set via portal or `az functionapp config appsettings set`)
- **Worker**: `APPLICATIONINSIGHTS_CONNECTION_STRING` (env var or set in container env)
- **FastAPI**: `APPLICATIONINSIGHTS_CONNECTION_STRING` (env var or set in container env)

If unset, services fall back to console logging (suitable for local dev/testing).

## Deployment

### Function App
```bash
func azure functionapp publish ai200-doc-func-6477
```

### Worker (as container or local)
```bash
# Local:
cd worker && source .venv/bin/activate
export APPLICATIONINSIGHTS_CONNECTION_STRING="..."
python worker.py

# Or in Azure Container Instance / App Service:
az acr build --registry ca89a0e372e0acr --image worker:latest --file Dockerfile .
```

### FastAPI Container App
```bash
az containerapp up \
  --name ai200-document-api-7659 \
  --resource-group rg-ai200-practice \
  --location eastus \
  --environment env-ai200-practiceeastusz \
  --registry-server ca89a0e372e0acr.azurecr.io \
  --repo-url https://github.com/youruser/repo \
  --source ./api
```

Make sure to set `APPLICATIONINSIGHTS_CONNECTION_STRING` in the environment for each service.

## Testing the instrumentation

### 1. Upload a document

```bash
az storage blob upload --account-name stai200practice9485 \
  --container-name uploads \
  --name test.pdf \
  --file /path/to/test.pdf
```

### 2. Check logs in Application Insights

In Azure Portal → Application Insights → Logs, run:

```kusto
traces
| where customDimensions.correlation_id != "-"
| order by timestamp asc
| project timestamp, severityLevel, message, customDimensions.correlation_id
```

### 3. Filter by specific document

```kusto
traces
| where customDimensions.document_id == "<PASTE_DOCUMENT_ID>"
| order by timestamp asc
```

You'll see the full trace: Function blob → Service Bus → Worker ingest → Cosmos writes.

### 4. Test error scenarios (Day 6 plan requirement)

**Scenario A: Deny DB access**
- Remove or revoke the worker's Cosmos role in RBAC
- Upload a document
- Query: `traces | where severityLevel >= 2 and message contains "Cosmos"`
- Verify clear error + correlation ID for debugging

**Scenario B: Poison message**
- Manually send bad JSON to Service Bus queue
- Worker abandons / dead-letters it
- Query: `traces | where message contains "dead-letter" or message contains "Malformed"`
- Verify message counted + correlation tracked

**Scenario C: Duplicate delivery**
- Crash worker before `complete_message` (use `--simulate-crash` flag)
- Message retried, worker processes again
- Query: `traces | where message contains "Duplicate delivery detected"`
- Verify idempotency (no double-processing)

## Success criteria (Day 6 plan)

- ✅ Application Insights enabled on Function + Container App
- ✅ Correlation ID threaded through API → Function → Service Bus → Worker → DB
- ✅ Tracked: request duration, function failures, queue depth, dead-letter count, worker failures, DB latency, Redis hit ratio, AI token usage
- ✅ 8 KQL queries for: failed requests, slowest API, exceptions, by operation, by correlationId
- ✅ Ready to run deliberate breakage + symptom → metric → log query → fix

## Next session

1. Ensure Postgres server is running + firewall allows your IP
2. Re-provision Redis (if needed for `ask.py` testing)
3. Deploy changes to Function App, Container App, and Worker
4. Upload a test document
5. Run KQL queries above to verify end-to-end tracing
6. Break the system as per Day 6 plan: deny DB access, stop worker, deploy bad revision
7. Document: symptom → metric → log query → fix for each scenario
