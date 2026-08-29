#!/bin/bash
# Non-secret config only -- all actual secrets (Postgres password, OpenAI key,
# Cosmos key) are fetched at runtime from Key Vault via managed identity /
# the developer's `az login` credential. Nothing sensitive lives in this file.
set -euo pipefail
cd "$(dirname "$0")"

export STORAGE_ACCOUNT_NAME="stai200practice9485"
export SERVICE_BUS_NAMESPACE_FQDN="sb-ai200-practice-2620.servicebus.windows.net"
export COSMOS_ENDPOINT="https://ai200-docdb-cosmos.documents.azure.com:443/"
export KEY_VAULT_URL="https://kv-ai200-2294.vault.azure.net/"
export PG_HOST="ai200-docdb-pg.postgres.database.azure.com"
export PG_USER="pgadmin"
export AZURE_OPENAI_ENDPOINT="https://ai200-openai-9050.openai.azure.com/"
export REDIS_HOST="ai200-doc-redis.eastus.redis.azure.net"
export REDIS_AUTH_OBJECT_ID="5d29b1c6-cea9-45a9-8e6c-f76feda64050"

source .venv/bin/activate
streamlit run app.py
