# Day 6: Monitoring + Observability

Instrumenting the pipeline with Application Insights, distributed tracing via correlation IDs, and Application Performance Monitoring (APM) queries for troubleshooting.

## What's been wired up

### Correlation ID threading (end-to-end tracing)

A unique `correlation_id` UUID is generated at the pipeline's entry point (Function App's blob trigger) and propagated through every component:

1. **Function App** (`function-app/function_app.py`):
   - Generates `correlation_id` on blob upload
   - Stores it in the Cosmos status record
   - Sends it as a Service Bus message application property

2. **Service Bus message**:
   - Carries `correlation_id` in `application_properties`
   - Worker reads it on dequeue

3. **Worker** (`worker/worker.py`):
   - Extracts `correlation_id` from Service Bus message
   - Sets it as a Python `contextvars.ContextVar` so every subsequent log in that processing path carries it automatically
   - Stores it in the Cosmos status record at each stage (processing → completed)

4. **FastAPI service** (`api/main.py`):
   - HTTP middleware extracts `X-Correlation-Id` from request headers (or generates one)
   - Sets it in the response header for client tracking
   - Every log carries it automatically via the same logging filter

### Logging instrumentation

Every service now logs with structured correlation IDs:

```python
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s %(levelname)s [correlation_id=%(correlation_id)s] %(message)s"
)
logging.getLogger().addFilter(CorrelationIdFilter())
```

This ensures:
- **Function App**: Blob trigger logs, Service Bus send logs, Cosmos writes all include custom dimensions
- **Worker**: Every message processing step, blob download, ingest, cache invalidation, Cosmos write logs the correlation ID
- **FastAPI**: Every endpoint request, response, error logs the correlation ID

### Application Insights wiring

Three separate instrumentation points:

1. **Function App** (`function-app/function_app.py`):
   - Uses Azure Functions' built-in App Insights bridge (automatic once `APPLICATIONINSIGHTS_CONNECTION_STRING` is set in app settings)
   - Logs with `extra={"custom_dimensions": {"correlation_id": ..., "document_id": ...}}`

2. **Worker** (`worker/telemetry.py`):
   - Uses `azure-monitor-opentelemetry` to send logs + traces directly to App Insights
   - Configured when `APPLICATIONINSIGHTS_CONNECTION_STRING` is present
   - All logs with correlation IDs are exported as traces

3. **FastAPI API** (`api/main.py`):
   - Uses `azure-monitor-opentelemetry` to instrument HTTP requests, exceptions, and logs
   - Middleware sets `X-Correlation-Id` header for inter-service correlation

### Token usage tracking

- `worker/ingest.py`: Logs `Query embedding token usage: total_tokens=X` on every chunk embedding
- `worker/search.py`: Logs `Query embedding token usage: total_tokens=X` on every semantic search
- These logs flow through the same correlation ID context, so token usage is tied to a specific request

## Key metrics tracked

Query Application Insights (Logs section) for:

| What | Custom dimension | Example value |
|------|------------------|---|
| Document ID | `document_id` | UUID |
| Request correlation ID | `correlation_id` | UUID |
| Processing stage | `status` field in Cosmos | `uploaded` → `processing` → `completed` |
| Queue depth (dead letters) | Service Bus message count | Count of messages in DLQ |
| Blob size | `size_bytes` in Cosmos | Bytes |
| Chunk count | `chunk_count` in Cosmos | Count |
| Embedding token usage | Logged by `ingest.py` / `search.py` | Total tokens |
| Redis cache hit/miss | Logged by `ask.py` | Cache hit: true/false |

## KQL queries for troubleshooting

### 1. All requests for a single correlation ID

```kusto
traces
| where customDimensions.correlation_id == "<CORRELATION_ID_HERE>"
| order by timestamp asc
| project timestamp, severityLevel, message, operation_ParentId, operation_Id
```

**Use case:** Trace a single document through the entire pipeline. Shows exact sequence of Function → Service Bus → Worker → Cosmos → DB.

### 2. Failed requests (errors + exceptions)

```kusto
traces
| where severityLevel >= 2
| summarize count() by tostring(customDimensions.document_id), message
| order by count_ desc
```

**Use case:** Find which documents or operations are failing most. Useful for identifying poison messages or recurring bugs.

### 3. Queue depth and dead-letter analysis

```kusto
traces
| where message contains "dead-letter" or message contains "abandon"
| summarize count() by tostring(customDimensions.document_id), message
| order by count_ desc
```

**Use case:** Track Service Bus retries and dead-letter events. High count suggests producer/consumer mismatch or poison message.

### 4. Slowest processing operations

```kusto
traces
| where customDimensions.status == "processing" or customDimensions.status == "completed"
| extend document_id = tostring(customDimensions.document_id)
| summarize 
    first_log = min(timestamp), 
    last_log = max(timestamp), 
    duration_ms = (max(timestamp) - min(timestamp)) / 1ms
    by document_id
| order by duration_ms desc
| project document_id, duration_ms
```

**Use case:** Identify outlier documents taking unusually long. Filter slowest -> slowest PDFs -> investigate parse logic.

### 5. Embedding/search token usage by operation

```kusto
traces
| where message contains "token usage" or message contains "embedding"
| summarize total_tokens = sum(toint(extract(@"total_tokens=(\d+)", 1, message)))
    by bin(timestamp, 1h)
| order by timestamp desc
```

**Use case:** Track daily AI token spend and detect cost anomalies (e.g., sudden spike = bug sending too many chunks).

### 6. Cache performance

```kusto
traces
| where message contains "Cache hit" or message contains "Cache miss"
| extend hit = iff(message contains "hit", 1, 0)
| summarize cache_hits = sum(hit), cache_misses = sum(1 - hit)
    by bin(timestamp, 1h)
| extend hit_rate = 100.0 * cache_hits / (cache_hits + cache_misses)
| project timestamp, cache_hits, cache_misses, hit_rate
| order by timestamp desc
```

**Use case:** Monitor cache effectiveness. If hit_rate drops, either cache was invalidated or questions are too diverse.

### 7. API latency by endpoint

```kusto
traces
| where operation_Name in ("GET /documents/{document_id}", "POST /documents")
| extend response_time_ms = (todatetime(timestamp) - todatetime(timestamp)) * 1000
| summarize
    p50 = percentile(response_time_ms, 50),
    p95 = percentile(response_time_ms, 95),
    p99 = percentile(response_time_ms, 99),
    avg = avg(response_time_ms),
    count = count()
    by tostring(operation_Name)
```

**Use case:** SLA monitoring. Detect API slowdowns or Container App resource contention.

### 8. By-correlation error trace (for debugging a specific failure)

```kusto
traces
| where customDimensions.correlation_id == "<CORRELATION_ID>" and severityLevel >= 2
| order by timestamp asc
| project timestamp, severityLevel, message, customDimensions
```

**Use case:** When a user reports "my document upload failed", paste its correlation ID into this query to see exact error + context.

## Environment variables required

Each service reads from environment:

- `APPLICATIONINSIGHTS_CONNECTION_STRING` — instrumentation key and endpoint (set via Azure portal or `az functionapp config appsettings set`)
- For worker: all existing ones + this one
- For FastAPI: new dependency on `azure-monitor-opentelemetry`

If the env var is not set, the service falls back to console logging (no App Insights). This lets it run locally or in dev without secrets.

## Next steps

1. **Deploy changes** (`az containerapp up` for API, `az functionapp deployment` for Function/Worker)
2. **Upload a test document** to trigger the full pipeline
3. **In Azure Portal**, go to Application Insights → Logs and run the queries above
4. **Break something intentionally**:
   - Stop the consumer worker → watch Service Bus dead-letter queue fill
   - Deny DB access in Key Vault → watch ingest fail with clear error + correlation ID
   - Force a re-delivery → verify idempotency (duplicate flag in Cosmos)
5. **Record symptom → metric → log query → fix** for each scenario (as per Day 6 plan)

## References

- [Azure Monitor + OpenTelemetry Python](https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-python)
- [Application Insights KQL reference](https://learn.microsoft.com/en-us/azure/data-explorer/kusto/query/)
- [Distributed tracing best practices](https://opentelemetry.io/docs/concepts/signals/traces/)
