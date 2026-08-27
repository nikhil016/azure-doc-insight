# Day 4: semantic vs keyword search comparison

Test document: `AI200_7Day_Practice_Plan.pdf` (11 chunks, 3 pages), embedded with
`text-embedding-3-small`. Semantic search uses pgvector cosine distance
(`embedding <=> query_embedding`); keyword search uses Postgres full-text
search (`to_tsvector`/`to_tsquery`, ranked by `ts_rank`).

| # | Question | Semantic top-1 | Keyword top-1 | Notes |
|---|---|---|---|---|
| 1 | What Azure services are used on Day 1? | page 1, correct | page 1, correct | Tie — literal terms present in the same chunk as the answer |
| 2 | How do I fix the containerapp extension error? | page 1, correct | page 1, correct | Tie — exact phrase match |
| 3 | What should I do with least-privilege RBAC roles? | page 2, correct (score 0.637, clear winner) | page 3, **wrong chunk** (generic "role" match, real answer ranked 3rd) | Semantic wins — paraphrased question, no literal keyword overlap with the target chunk |
| 4 | What KQL queries should I write for monitoring? | page 3, right page but 2nd-best chunk ranked #1 (correct chunk close #2, 0.384 vs 0.394) | page 3, correct chunk ranked #1 (exact "KQL" term match) | Keyword wins — rare acronym diluted inside a longer semantic embedding, but stands out lexically |
| 5 | How do I clean up resources at the end? | page 3, correct | page 3, correct | Tie |

**Takeaway:** all 5 questions retrieved the correct *page* with both methods.
Semantic search clearly wins when the question is paraphrased and shares no
vocabulary with the source text (Q3). Keyword search wins when the answer
hinges on a rare, exact term like an acronym that a longer chunk's embedding
dilutes (Q4). In production this argues for hybrid retrieval (vector +
keyword, e.g. reciprocal rank fusion) rather than picking one exclusively —
matches how Cosmos DB's own vector search and Azure AI Search both support
hybrid queries.
