"""End-to-end test: real FastAPI app + real LangGraph + in-memory Qdrant + SQLite.

Only the LLM and the embedding model are stubbed, so it runs in seconds with no
GPU, API key, Docker or network. From the backend folder:

    pip install -r requirements.txt -r requirements-dev.txt
    python tests/e2e_test.py
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time

WORK = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{WORK}/test.db",
    "QDRANT_URL": ":memory:",
    "REDIS_URL": "",
    "INGEST_MODE": "inline",
    "UPLOAD_DIR": f"{WORK}/uploads",
    "SECRET_KEY": "test",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---- fake embeddings: bag-of-words hashed into 384 dims (similar text -> similar vector)
import numpy as np
import app.services.embeddings as emb


def fake_vec(text):
    v = np.zeros(384)
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 384] += 1
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


emb.embed_texts = lambda texts: [fake_vec(t) for t in texts]
emb.embed_query = lambda t: fake_vec(t)
emb.embedding_dim = lambda: 384

# ---- fake LLM that behaves like a cooperative model, keyed off the system prompt
import app.services.llm as llm
from app.services import agent as agent_mod

CALLS = []


async def fake_generate(prompt, system=None, json_mode=False):
    CALLS.append(system[:30] if system else "none")
    out = ""
    if system == agent_mod.ROUTER_SYSTEM:
        q = prompt.split("Question:")[-1].lower()
        route = "chitchat" if q.strip().startswith(("hi", "hello")) else \
                "multi_doc" if "compare" in q else "single"
        out = json.dumps({"route": route})
    elif system and system.startswith("You break a complex question"):
        out = json.dumps({"sub_questions": [
            {"question": "What is the termination notice period in the Acme contract?", "filename": "acme_contract.txt"},
            {"question": "What is the termination notice period in the Globex contract?", "filename": "globex_contract.txt"},
        ]})
    elif system == agent_mod.GRADER_SYSTEM:
        q = prompt.split("Chunks:")[0].lower()
        chunks = re.split(r"\n\n(?=\[\d+\])", prompt.split("Chunks:\n", 1)[1])
        keys = [w for w in ("termination", "revenue", "refund", "notice") if w in q]
        rel = [i + 1 for i, c in enumerate(chunks) if any(k in c.lower() for k in keys)]
        out = json.dumps({"relevant": rel})
    elif system == agent_mod.REWRITE_SYSTEM:
        out = json.dumps({"query": "termination notice period days"})
    elif system == agent_mod.CHITCHAT_SYSTEM:
        out = "Hello! Ask me anything about your documents."
    else:
        n = len(re.findall(r"^\[\d+\]", prompt, re.M))
        out = f"Grounded answer using {n} sources [1]."
    await asyncio.sleep(0)
    return {"text": out, "tokens": len(out.split()), "duration_ms": 5.0}


llm.generate = fake_generate

# ---- create test documents
import fitz

docs = {
    "acme_contract.txt": "Acme Services Agreement\n\nTermination: either party may terminate with 30 days written notice.\n\nPayment terms are net 45 days from invoice date.",
    "globex_contract.txt": "Globex Master Agreement\n\nTermination requires 90 days notice in writing to the other party.\n\nGlobex refund policy: refunds within 14 days.",
    "sales.csv": "region,quarter,revenue\nEMEA,Q1,120000\nAPAC,Q1,95000\nNA,Q1,210000\n",
}
pdf = fitz.open()
page = pdf.new_page()
page.insert_text((72, 72), "Quarterly Report", fontsize=22)
page.insert_text((72, 110), "Revenue grew 18 percent year over year driven by EMEA expansion.", fontsize=11)
page.insert_text((72, 126), "Operating costs were flat while headcount rose modestly this quarter.", fontsize=11)
pdf.save(f"{WORK}/report.pdf")

from fastapi.testclient import TestClient
from app.main import app

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  -> {detail}" if detail else ""))


with TestClient(app) as c:
    r = c.post("/auth/register", json={"org_name": "Acme Corp", "email": "a@acme.com", "password": "pw123456"})
    check("register", r.status_code == 200, r.text[:100])
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # second tenant to prove isolation
    r2 = c.post("/auth/register", json={"org_name": "Other Inc", "email": "b@other.com", "password": "pw123456"})
    H2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

    for name, content in docs.items():
        r = c.post("/documents", headers=H, files={"file": (name, content.encode(), "text/plain")})
        check(f"upload {name}", r.status_code == 202, r.text[:80])
    with open(f"{WORK}/report.pdf", "rb") as f:
        r = c.post("/documents", headers=H, files={"file": ("report.pdf", f.read(), "application/pdf")})
    check("upload report.pdf", r.status_code == 202)

    lst = c.get("/documents", headers=H).json()
    check("all 4 indexed (inline ingestion)", all(d["status"] == "indexed" for d in lst),
          [(d["filename"], d["status"], d["num_chunks"], d["error"]) for d in lst])

    # 1. chit-chat route skips retrieval
    r = c.post("/chat", headers=H, json={"query": "hello there"}).json()
    m = r["metrics"]
    check("route chitchat", m["route"] == "chitchat" and m["chunks_retrieved"] == 0, m["trace"])

    # 2. single route with grading
    r = c.post("/chat", headers=H, json={"query": "What was revenue by region?"}).json()
    m = r["metrics"]
    check("route single + grounded sources", m["route"] == "single" and len(r["sources"]) >= 1,
          f"{m['trace']} sources={[s['filename'] for s in r['sources']]}")

    # 3. multi-doc decomposition across two contracts
    r = c.post("/chat", headers=H, json={"query": "Compare the termination terms of the two contracts"}).json()
    m = r["metrics"]
    files = {s["filename"] for s in r["sources"]}
    check("route multi_doc + 2 sub-questions", m["route"] == "multi_doc" and len(m["sub_questions"]) == 2, m["trace"])
    check("multi_doc evidence from BOTH contracts", {"acme_contract.txt", "globex_contract.txt"} <= files, files)

    # 4. self-correction loop: question whose first search finds nothing relevant
    r = c.post("/chat", headers=H, json={"query": "How long before ending the deal must we inform them?"}).json()
    m = r["metrics"]
    check("self-correction rewrite loop ran", m["rewrites"] >= 1 and m["agent_iterations"] == 2, m["trace"])

    # 5. tenant isolation: other org sees nothing
    r = c.post("/chat", headers=H2, json={"query": "What is the termination notice period?"}).json()
    check("tenant isolation (no leaked sources)", r["sources"] == [], r["metrics"]["trace"])

    # 6. metrics + graph endpoints
    ov = c.get("/metrics/recent", headers=H)
    check("metrics/recent", ov.status_code == 200 and ov.json()[0]["route"], ov.text[:120])
    g = c.get("/agent/graph").json()["mermaid"]
    check("graph endpoint", "rewrite" in g and "decompose" in g)
    open(f"{WORK}/graph.mmd", "w").write(g)

    # 7. delete removes vectors
    acme = next(d for d in lst if d["filename"] == "acme_contract.txt")
    check("delete", c.delete(f"/documents/{acme['id']}", headers=H).status_code == 204)

print("\nLLM calls by prompt:", {k: CALLS.count(k) for k in set(CALLS)})
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
