import sys
import time
from pathlib import Path
from uuid import uuid4

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))

from azure_clients import get_blob_service_client, get_documents_container  # noqa: E402
from cache import get_redis_client, invalidate_document  # noqa: E402
from ask import answer_question  # noqa: E402
import worker as worker_module  # noqa: E402

st.set_page_config(page_title="AI-200 Document Insight", layout="wide")
st.title("AI-200 Document Insight — pipeline dashboard")
st.caption("One control panel over Days 1-5: upload -> blob trigger -> queue -> worker -> Cosmos status -> pgvector search -> Redis cache")


@st.cache_resource
def documents_container():
    return get_documents_container()


def redis_client():
    # Not cached: Redis auth uses a short-lived Entra ID token (~1hr TTL).
    # Caching the client would cache the stale token past its expiry and
    # every call would fail with "invalid username-password pair".
    return get_redis_client()


def list_documents():
    container = documents_container()
    items = list(container.read_all_items())
    items.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return items


# --- Upload ---
st.header("1. Upload a document")
uploaded_file = st.file_uploader("Upload a file to the `uploads` container", type=None)
if uploaded_file is not None and st.button("Upload"):
    blob_service = get_blob_service_client()
    blob_name = uploaded_file.name
    blob_client = blob_service.get_blob_client(container="uploads", blob=blob_name)
    blob_client.upload_blob(uploaded_file.getvalue(), overwrite=True)
    st.success(f"Uploaded {blob_name}. The blob-triggered Function will pick it up shortly (may take up to ~1-2 min on a cold Consumption instance).")

st.divider()

# --- Document status table ---
st.header("2. Document status")
col_a, col_b = st.columns([1, 1])
with col_a:
    if st.button("Refresh status"):
        st.rerun()
with col_b:
    if st.button("Process pending queue now"):
        with st.spinner("Draining the Service Bus queue (downloading, chunking, embedding, invalidating cache)..."):
            try:
                worker_module.run(max_messages=20)
                st.success("Queue drained (or was already empty).")
            except Exception as exc:
                st.error(f"Worker run failed: {exc}")
        st.rerun()

documents = list_documents()
if not documents:
    st.info("No documents yet. Upload one above.")
else:
    st.dataframe(
        [
            {
                "id": d["id"],
                "blob_name": d.get("blob_name"),
                "status": d.get("status"),
                "chunk_count": d.get("chunk_count"),
                "bytes": d.get("bytes") or d.get("size_bytes"),
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at"),
            }
            for d in documents
        ],
        width="stretch",
        hide_index=True,
    )

st.divider()

# --- Ask questions ---
st.header("3. Ask a question")
with st.form("ask_form"):
    question = st.text_input("Question")
    user_id = st.text_input("User ID (for cache key)", value="dashboard-user")
    completed_ids = [d["id"] for d in documents if d.get("status") == "completed"]
    document_filter = st.selectbox("Restrict to one document (optional)", ["(all documents)"] + completed_ids)
    submitted = st.form_submit_button("Ask")

if submitted and question:
    doc_id = None if document_filter == "(all documents)" else document_filter
    start = time.time()
    try:
        with st.spinner("Thinking... (first question after a restart can take a while -- it's fetching secrets and warming up Azure AD auth, not stuck)"):
            result = answer_question(redis_client(), user_id or "dashboard-user", question, document_id=doc_id)
        elapsed_ms = (time.time() - start) * 1000
        badge = "CACHE HIT" if result.get("cache_hit") else "CACHE MISS (fresh embed + search)"
        st.markdown(f"**{badge}** — {elapsed_ms:.0f}ms")
        if result.get("top_content"):
            st.write(result["top_content"])
            st.caption(f"Source: document {result['top_document_id']}, page {result['top_page']}, score {result['score']}")
            with st.expander("All retrieved sources"):
                st.json(result["sources"])
        else:
            st.warning("No matching chunks found.")
    except Exception as exc:
        st.error(f"Question failed: {exc}")

st.divider()

# --- Cache invalidation ---
st.header("4. Cache invalidation")
if documents:
    invalidate_id = st.selectbox("Invalidate cached answers for document", [d["id"] for d in documents], key="invalidate_select")
    if st.button("Invalidate"):
        removed = invalidate_document(redis_client(), invalidate_id)
        st.success(f"Invalidated {removed} cached answer(s) for document {invalidate_id}.")
