# DocuSense — The Complete Study Guide

Everything you need to explain this project: what the product is, how each part works, why each technical choice was made, and how to talk about it. Read it once top to bottom, then use it as a reference before interviews.

---

## 1. The pitch

**Thirty-second version.** Companies drown in PDFs, contracts, invoices and spreadsheets. Finding one answer means opening ten files. DocuSense lets a company upload all of it and ask questions in plain English, like "how does the termination clause differ between our Acme and Globex contracts?", and get an answer in seconds that cites the exact file and page. It runs on open-source models that can be hosted on the company's own servers, so confidential data doesn't have to leave the building.

**Two-minute version.** DocuSense is a multi-tenant SaaS platform: many companies share one deployment, and each company's data is walled off from every other's, down to the vector database. Uploaded documents go through layout-aware ingestion that keeps tables and headings intact, so a balance sheet stays a table instead of turning into a jumble of numbers. Questions are handled by an agent built with **LangGraph**. The agent first decides what kind of question it's facing. Small talk gets a direct reply. A normal question gets one search. A comparison gets broken into one sub-question per document, each searched separately. The agent then grades every piece of evidence it found, throws out what's irrelevant, rewrites any search that came back empty, and only then writes an answer restricted to verified evidence, with citations. An admin dashboard tracks latency, throughput, cache efficiency, which paths the agent takes and user satisfaction, which is what operating an AI system in production looks like.

**Three differentiators to remember:**
- *Private by architecture*: the LLM can run on your own hardware.
- *Accurate by design*: the agent checks its own evidence before answering, and cites sources.
- *Operable*: real metrics, background ingestion, tenant isolation, free-tier deployable.

---

## 2. The problem, precisely

Keyword search (Ctrl+F, SharePoint search) matches words, not meaning. Search "termination fee" and you miss the contract that says "early exit penalty." Public AI chatbots understand meaning, but they have two problems for businesses: you must send confidential data to someone else's servers, and they sometimes invent confident, wrong answers ("hallucinate"). DocuSense addresses all three: meaning-based search (embeddings), privacy (self-hostable Llama), and grounding (answers must come from retrieved documents, with citations, or the system says it doesn't know).

The underlying technique is **RAG, Retrieval-Augmented Generation**: before asking the LLM, retrieve the relevant passages from your documents, then tell the LLM "answer using only these." Basic RAG does one search and hopes it found the right passages. DocuSense is **agentic RAG**: the system makes decisions along the way (which path to take, whether the evidence is good enough, whether to search again).

---

## 3. How it works in a nutshell

There are two pipelines. Understand both and you understand the app.

### Pipeline A — Ingestion (when a file is uploaded)

The upload endpoint saves the file, writes a database row with status `queued`, and responds immediately. The browser isn't kept waiting while a 200-page PDF is processed. The heavy work then runs in the background, in one of two places depending on the `INGEST_MODE` setting:
- `celery`: a separate **Celery worker** process takes the job from a **Redis** queue. This is the scalable setup used with docker-compose.
- `inline`: the API process runs it as a background task after replying. This is for free hosting, where you can't run a separate worker.

Both run the same function, `run_ingestion`, which does three things:

1. **Parse** (`parser.py`). For PDFs, PyMuPDF first *detects tables* and converts each to a Markdown table, so rows and columns survive. Those table areas are then skipped during normal text extraction so nothing is read twice. Text bigger than the page's typical font size is tagged as a *heading*. CSV and Excel files become Markdown tables in groups of 30 rows, with the header row repeated on every group so each group makes sense on its own. The output is an ordered list of blocks, each tagged heading, paragraph or table.

2. **Chunk** (`chunker.py`). Models can't read a whole book at once, so documents are split into chunks. Naive systems cut every 1,000 characters, which slices tables in half and separates sentences from their section titles. DocuSense chunks *by structure*: a heading closes the current chunk and its text is prepended to the following chunks as `[Section: Quarterly Results]`, so each chunk carries its context; tables are never split; paragraphs are packed up to about 1,600 characters with a small overlap between neighbours.

3. **Embed and index** (`embeddings.py`, `vectorstore.py`). Each chunk is turned into a list of 384 numbers (an **embedding**) by the bge-small model. Chunks with similar meaning get similar numbers. The embeddings go into **Qdrant**, a database built for finding the most similar vectors quickly. Each stored point also carries the chunk's text, page, filename and, critically, the **org_id**: the tenant tag.

The document's status then flips to `indexed`. The Documents page polls every 4 seconds, so the user watches it go queued → processing → indexed.

### Pipeline B — Answering (the LangGraph agent)

This is the heart of the project, in `backend/app/services/agent.py`.

**What LangGraph is.** LangGraph is a library for building AI agents as **graphs**. You define:
- a **state**: one shared dictionary that every step reads and updates (here: the question, the route chosen, the sub-questions, the evidence kept, timings);
- **nodes**: plain Python functions, one per step;
- **edges**: which node runs next. A normal edge always goes to the same node. A **conditional edge** calls a small function that looks at the state and picks the next node. Conditional edges are how you get decisions and loops.

You compile the graph once and then run it with an initial state. LangGraph handles running the nodes in order, passing the state along, and stopping at `END`.

**The graph:**

```
START → cache ──hit──────────────────────────────────────────────▶ END
          │ miss
          ▼
        router ──chitchat──▶ direct_answer ──────────────────────▶ END
          │ single                 │ multi_doc
          ▼                        ▼
        single               decompose
          └──────────┬─────────────┘
                     ▼
          ┌──────▶ retrieve
          │          ▼
          │        grade ──enough evidence──▶ synthesize ────────▶ END
          │          │ weak
          └─────── rewrite
```

**Step by step:**

1. **cache.** The question is embedded and compared with recently answered questions *from the same company*, stored in Redis. If one is essentially the same (similarity ≥ 0.95, so "what's our refund policy" matches "how do refunds work"), its answer is returned immediately and the LLM is never called. If Redis isn't configured or is down, this quietly counts as a miss; the cache can speed things up but can never break a query.

2. **router** *(agentic routing)*. The LLM sees the question plus the list of the company's document names and classifies it as:
   - `chitchat`: greetings or "what can you do?", where no search is needed;
   - `single`: a normal question that one search can answer;
   - `multi_doc`: a question that compares or combines several documents.
   A conditional edge sends the state down the matching path. Two safety rules: if the LLM returns anything unexpected, the route defaults to `single` (searching never hurts correctness), and `multi_doc` is downgraded to `single` when the company has fewer than two documents.

3. **direct_answer** (chit-chat path only). A short reply explaining what the assistant does, with no retrieval and no wasted grading calls.

4. **single** or **decompose** *(multi-document reasoning)*. On the single path, the question becomes one "sub-question". On the multi-doc path, the LLM breaks the question into 2 to 4 sub-questions and may tie each to one specific document: "Compare termination in Acme and Globex" becomes "termination terms in acme.pdf?" and "termination terms in globex.pdf?". Filenames the LLM names are kept only if they really exist, so an invented filename can't make a search return nothing. If decomposition fails, it falls back to treating the question as one.

5. **retrieve.** Every sub-question that still lacks evidence is searched, all in parallel. Each search is **hybrid**:
   - *dense search*: Qdrant finds the chunks whose embeddings are closest to the question's (good at meaning, weak at exact codes like "PO-7841");
   - *BM25*: classic keyword scoring over those candidates (good at exact terms, weak at synonyms);
   - the two rankings are merged with **Reciprocal Rank Fusion**: each chunk scores `1/(60 + rank)` in each list, and the scores are added.
   A sub-question tied to one document only searches that document, so in a comparison one contract can't crowd the other out of the results.

6. **grade** *(self-correction, part 1)*. Retrieval always returns *something*, even when nothing relevant exists. So for each sub-question, the LLM gets the question and all its numbered chunks in **one** call and answers with the numbers of the chunks that actually help. The rest are discarded before answer generation. Kept chunks accumulate across passes. If the grader replies with invalid JSON, the top chunks are kept anyway (*fail open*): noisy evidence beats an empty answer.

7. **The decision (conditional edge after grade).** Is there enough evidence? A single question wants at least 2 relevant chunks; each part of a multi-doc question needs at least 1. If every part is covered → **synthesize**. If not, and the retry budget isn't used up (2 passes by default) → **rewrite**. If the company has no indexed documents at all, it skips the retry, since no rewording can help.

8. **rewrite** *(self-correction, part 2)*. For each sub-question that came back empty, the LLM rephrases it with different keywords ("ending the deal" → "termination notice period"). If that sub-question was tied to one document, the retry searches all documents, in case it was tied to the wrong one. Then the graph loops back to **retrieve**. This edge, `rewrite → retrieve`, is the self-correcting loop.

9. **synthesize.** The kept chunks are numbered `[1]`, `[2]`… and grouped by sub-question, then sent to the LLM with strict instructions: answer only from this context, cite every claim, and say plainly when something can't be answered. For multi-doc questions it is also told to answer each part and then compare or combine them. If no evidence survived at all, the LLM isn't called; the user gets an honest "couldn't find this" message.

10. **Metrics.** Each node adds its timings and token counts to the state, and records a short label in a `trace` list, like `["cache:miss", "router:multi_doc", "decompose:2", "retrieve:16", "grade:3/2", "synthesize"]`. The API saves a `query_metrics` row with latency, the route, number of sub-questions, rewrites, cache hit, tokens/sec and more. The chat UI shows the route and an expandable "Agent steps" trace under each answer, and the 👍/👎 buttons write feedback back to the same row.

### Multi-tenancy, running through everything

Every company is an `Organization`. Every user's login token (JWT) carries their `org_id`. Every table has an `org_id` column and every query filters on it. Most importantly, the tenant filter is applied **inside Qdrant** on every search and delete, so the vector search physically can't return another company's chunks even if the application code had a bug. Cache keys are prefixed with the org too. This layered protection is exactly what interviewers probe when you say "multi-tenant."

---

## 4. Architecture map

```
Browser
  │
  ▼
Nginx (port 80) ── single entry point: /api/* → FastAPI, everything else → Next.js
  ├──▶ Next.js frontend (chat, documents, dashboard)
  └──▶ FastAPI backend
          ├── PostgreSQL ── users, orgs, documents, chat history, query metrics
          ├── Redis ─────── Celery task queue + semantic cache
          ├── Qdrant ────── vector database (chunks + embeddings, tenant-tagged)
          └── LLM ───────── Ollama (self-hosted) or an OpenAI-compatible API (Groq)
       Celery worker ────── ingestion jobs (or inline in the API on free hosting)
```

**Two deployment shapes, same code.**

| | Local / private (docker compose) | Free public demo |
|---|---|---|
| Frontend | Next.js container behind Nginx | Vercel |
| API | FastAPI container | Render free web service |
| Ingestion | Celery worker container | Inline background task |
| Postgres | Container | Neon |
| Vectors | Qdrant container | Qdrant Cloud |
| Cache | Redis container | Upstash (optional) |
| LLM | Ollama, Llama 3.1 8B | Groq, Llama 3.1 8B |

Environment variables select everything. That's a deliberate design point: configuration, not code changes, decides where the system runs.

---

## 5. Every file, explained

### Root
- `docker-compose.yml`: all services for local or self-hosted runs, with persistent volumes, a Postgres health check (the API waits until the database is ready), and the `host.docker.internal` mapping that lets containers reach Ollama on your laptop.
- `.env.example`: every setting, with defaults that work locally. Copy it to `.env`, which Git ignores.
- `render.yaml`: a Render "Blueprint" describing the free API deployment; secrets are typed into Render's dashboard, never committed.
- `nginx/nginx.conf`: the reverse proxy. Strips the `/api` prefix, allows 100 MB uploads, and uses 300-second timeouts because LLM answers on a laptop are slow.
- `README.md` (the repo's front page), `DEPLOY.md` (GitHub + free hosting), this guide.

### Backend — `backend/app/`

**`main.py`** — creates the FastAPI app, wires up the route modules and CORS (which browser origins may call the API), creates the upload folder and database tables at startup, and serves `/health` and `/agent/graph` (the compiled LangGraph as a Mermaid diagram).

**`core/config.py`** — every environment variable as a typed setting with a default, via Pydantic Settings. Includes the switches `LLM_PROVIDER`, `INGEST_MODE`, `QDRANT_API_KEY`, `REDIS_URL` (empty disables the cache) and the agent tuning knobs.

**`core/security.py`** — passwords hashed with bcrypt; login issues a signed **JWT** containing user id, org id and role, with an expiry. Protected endpoints declare `Depends(get_current_user)` to get the decoded user. `require_admin` protects the dashboard.

**`db/database.py`** — creates two database connections from one URL: an *async* one for FastAPI (the server keeps handling other requests while waiting on the database) and a *sync* one for ingestion (Celery tasks aren't async). It accepts URLs in any common format, including Neon's `postgresql://…?sslmode=require`, and converts them for each driver.

**`db/models.py`** — six tables: `organizations` (tenant root), `users`, `documents` (with status queued → processing → indexed/failed), `chat_sessions`, `chat_messages` (including cited sources), and `query_metrics` (one row per question: latency, route, sub-questions, rewrites, tokens/sec, cache hit, feedback).

**`schemas/schemas.py`** — request and response shapes. FastAPI rejects invalid input automatically and generates the `/docs` page from these.

**`api/routes_auth.py`** — register (the first user of a new organization becomes its admin) and login.
**`api/routes_documents.py`** — upload with a file-type whitelist and a size cap, saved under a per-tenant folder with a random filename, then handed to Celery or an inline background task depending on `INGEST_MODE`. Also list and delete (delete removes the vectors, the file and the row, after checking the document belongs to the caller's org).
**`api/routes_chat.py`** — loads the org's indexed document names (the router and decomposer need them), runs the agent, saves both messages and the metrics row, and returns the metric id so the UI can attach feedback.
**`api/routes_metrics.py`** — admin-only aggregates: 7-day counts, average and p95 latency, tokens/sec, cache hit rate, satisfaction, the route mix, an hourly time series and the recent-queries list.

**`services/agent.py`** — the LangGraph agent described in section 3: state definition, prompts, nine nodes, three conditional edges, the compiled graph, and `answer_query`, which runs the graph and packages the answer, sources and metrics.
**`services/llm.py`** — one `generate()` function with two backends: Ollama, or any OpenAI-compatible API. Both return text, token count and duration, so the agent doesn't know or care which is running. The API backend retries with a wait when a free tier says "too many requests" (HTTP 429).
**`services/parser.py`** — layout-aware extraction for PDFs, CSV/XLSX and text.
**`services/chunker.py`** — structure-aware chunking.
**`services/embeddings.py`** — runs bge-small through **fastembed**, which uses ONNX Runtime instead of PyTorch and needs a fraction of the memory. Queries get bge's recommended instruction prefix.
**`services/vectorstore.py`** — creates the Qdrant collection and payload indexes, stores chunks with deterministic IDs (re-ingesting a document overwrites its old vectors), and runs the tenant-filtered hybrid search, optionally scoped to one filename.
**`services/cache.py`** — the semantic cache, which degrades to "miss" whenever Redis is absent or failing.

**`workers/celery_app.py`** — Celery configured with `task_acks_late` (a job goes back on the queue if a worker crashes mid-job) and `prefetch_multiplier=1` (each worker takes one heavy job at a time).
**`workers/tasks.py`** — `run_ingestion` (parse → chunk → embed → index, recording failures on the document row so users see why) and the thin Celery task that wraps it with retries.

**`tests/e2e_test.py`** — an end-to-end test of the real API and real LangGraph against SQLite and in-memory Qdrant, with the LLM and embeddings stubbed so it runs in seconds offline. It checks ingestion of each file type, all three routes, that a comparison pulls evidence from both documents, the rewrite loop, tenant isolation, metrics and deletion.

**`Dockerfile`** — installs dependencies, downloads the embedding model into the image at build time (so a cold start doesn't wait for a download), and starts on whatever port the host assigns (`$PORT`).

### Frontend — `frontend/`

- **`lib/api.ts`** — the single place the frontend makes HTTP calls: attaches the JWT, redirects to login when the session expires, and exposes typed functions.
- **`app/layout.tsx`, `components/Nav.tsx`** — the page shell and navigation.
- **`app/login/page.tsx`** — sign in, or create a workspace.
- **`app/page.tsx`** — the chat: answers with citations, sub-questions for multi-doc answers, an expandable "Agent steps" trace, a strip showing the route, latency, tokens/sec, "cached" and "self-corrected", and feedback buttons.
- **`app/documents/page.tsx`** — upload area plus a document list that refreshes every 4 seconds to show ingestion progress.
- **`app/dashboard/page.tsx`** — the operations dashboard built with **Tremor**: KPI cards, routing mix, an hourly chart, recent queries with their routes.
- Config files are standard Next.js; the `Dockerfile` builds a small standalone server image.

---

## 6. Technical decisions (your interview answers)

**Why LangGraph?** The agent has decisions (which route), fan-out (several sub-questions in parallel) and a loop (rewrite and retry). In plain Python that becomes nested `if`s and `while` loops that are hard to follow and test. LangGraph makes each step a named node and each decision a conditional edge, so the control flow is explicit, each node can be tested on its own, the graph can be drawn automatically, and adding a new path means adding a node and an edge.

**Why not CrewAI?** CrewAI models teams of role-playing agents ("researcher", "writer") that delegate to each other. That's useful for open-ended collaboration, but this is a controlled retrieval workflow where you want predictable steps, bounded loops and precise metrics. LangGraph fits that exactly.

**Why does LangGraph run without LangChain here?** LangGraph doesn't need LangChain. The nodes call a small in-house LLM client, which keeps dependencies light, makes switching providers one setting, and lets us capture exact token counts and timings for the dashboard.

**Why route at all?** Without a router, "hi" triggers a full search and several grading calls, wasting time and money. And comparison questions need a different strategy (decomposition) than simple lookups. Routing gives each question type the right strategy.

**Why decompose comparison questions?** One search for "compare A and B" often returns five chunks from A and none from B, because one document matches the wording better. Searching each document separately guarantees evidence from both before the comparison is written.

**Why batched grading?** Grading 8 chunks one at a time costs 8 LLM calls; grading them in one prompt costs 1. That cuts latency and fits within free-tier API rate limits, which count requests per minute.

**Why cap the retry loop at 2 passes?** Unbounded agent loops are a production risk: cost, latency, and possibly never stopping. Two passes catch most badly worded questions. After that, an honest "not found" is better than thrashing. LangGraph's recursion limit is a second safety net.

**Why hybrid search with RRF?** Embeddings are fuzzy: great at meaning, weak at exact identifiers. BM25 is the opposite. Enterprise users ask both kinds of questions. Their scores live on different scales (cosine similarity vs unbounded BM25), so averaging them is meaningless; RRF uses only rank positions, which are always comparable.

**Why Qdrant?** It's built for vector search (fast approximate nearest-neighbour indexes) with first-class payload filtering, which is what makes enforcing the tenant filter inside the search cheap. A fair follow-up answer: Postgres with pgvector is a reasonable simpler choice at small scale.

**Why fastembed instead of sentence-transformers?** Same bge-small model, but on ONNX Runtime instead of PyTorch: a few hundred MB of memory instead of about 1.5 GB, and a much smaller image. That's what lets the API run on a 512 MB free instance.

**Why a pluggable LLM provider?** The private-deployment pitch needs self-hosted Ollama; a free public demo can't include a GPU. One setting switches between them with identical agent behaviour. It's also how you'd add vLLM or another provider later.

**Why Celery and an inline mode?** Celery is the production pattern for slow jobs: survives crashes, scales by adding workers. Free hosts don't offer worker processes, so the same ingestion function can also run as a background task inside the API. Same code, chosen by configuration.

**Why a semantic cache that can fail silently?** Companies ask the same questions repeatedly; paraphrase matching at 0.95 similarity catches rewording without serving answers to genuinely different questions. Making it optional means a cache outage slows the system down instead of taking it down.

**Why store metrics in Postgres instead of a metrics system like Prometheus?** The dashboard needs metrics joined with business data: per tenant, per route, joined to feedback and query text. That's a relational query. (Good follow-up: you'd add Prometheus/Grafana for infrastructure metrics like CPU and memory.)

**Why JWTs?** They're self-contained, so any API replica can validate one without a shared session store, which lets the API scale horizontally.

**Limitations to volunteer (this earns trust):**
- Tables are created with `create_all`; real production would use Alembic migrations.
- Answers arrive all at once; streaming token by token would feel faster.
- No OCR, so scanned image-only PDFs can't be read.
- The semantic cache scans up to 200 entries per lookup; at scale it would need its own vector index.
- On the free Groq tier, heavy usage hits the tokens-per-minute limit, which the client handles by waiting and retrying.
- The free demo sends text to Groq; the private deployment uses Ollama to keep everything in-house.

---

## 7. How to demo it (two minutes)

1. Show the Documents page with two contracts and a spreadsheet indexed.
2. Ask "What was Q3 revenue by region?" → point at the `single` route badge and the citations.
3. Ask "Compare the termination terms in the two contracts" → open the sub-questions and "Agent steps": `router:multi_doc → decompose:2 → …`. Point out that the sources come from both files.
4. Ask something vaguely worded ("how much warning before ending the deal?") → show "self-corrected" and the `grade:0 → rewrite → retrieve` loop in the trace.
5. Ask the revenue question again → "cached", about 40 ms.
6. Open the Dashboard → latency, p95, routing mix, feedback.
7. Open `/agent/graph` or the README diagram → "this is the LangGraph graph those traces walked through."

---

## 8. Glossary

**RAG** — retrieve relevant text first, then have the LLM answer from it. **Agentic RAG** — RAG where the system makes decisions mid-way (route, grade, retry). **LangGraph** — a library for building agents as graphs of steps with shared state. **Node** — one step in the graph (a function). **Conditional edge** — a branch whose destination is decided at runtime from the state. **State** — the shared dictionary every node reads and updates. **Embedding** — a list of numbers representing a text's meaning; similar meanings have similar numbers. **Vector database** — stores embeddings and finds the most similar ones fast. **BM25** — classic keyword relevance scoring. **RRF** — merging ranked lists by rank position. **Chunk** — a small piece of a document sized for search and prompting. **Hallucination** — an LLM inventing plausible-sounding falsehoods. **Grounding** — forcing answers to come from retrieved evidence. **Multi-tenant** — one deployment serving many isolated customers. **JWT** — a signed, self-contained login token. **Celery** — Python's standard background job queue. **p95 latency** — the time under which 95% of requests finish. **Ollama** — a local server that runs open-source LLMs. **Groq** — a hosted API serving open-source models very fast. **ONNX Runtime** — a lightweight engine for running ML models without PyTorch. **Tokens per second** — how fast the LLM generates text.
