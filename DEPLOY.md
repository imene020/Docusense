# Putting DocuSense on GitHub and deploying it for free

This guide has three parts, in this order:

1. **Push the code to GitHub.** The hosting services deploy straight from your repository.
2. **Create the free service accounts** (database, vector store, LLM).
3. **Deploy the backend to Render and the frontend to Vercel**, then connect them.

Budget about an hour the first time. Everything below was checked against each provider's free tier as of October 2026. Free tiers change, so if a screen looks different, follow the provider's own docs for that step.

---

## Part 1 — Upload to GitHub (from VS Code)

### 1.1 One-time setup

1. Install Git from https://git-scm.com (on Windows, accept the defaults). Restart VS Code afterwards.
2. Create a free account at https://github.com if you don't have one.
3. In the VS Code terminal (`` Ctrl+` ``), tell Git who you are:
   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

### 1.2 Create the repository on GitHub

1. Go to https://github.com/new.
2. Repository name: `docusense`. Visibility: **Public** (recruiters need to see it).
3. Leave "Add a README", ".gitignore" and "license" **unchecked**. The project already has them.
4. Click **Create repository** and keep the page open; you need its URL.

### 1.3 Push your code

In the VS Code terminal, inside the `docusense` folder:

```bash
git init
git add .
git status
```

**Check the `git status` output before going further.** You should see `backend/`, `frontend/`, `README.md` and so on. You must **not** see `.env`. That file holds your secrets, and `.gitignore` keeps it out. If `.env` appears, stop and make sure `.gitignore` contains a line reading `.env`.

Then:

```bash
git commit -m "DocuSense: agentic RAG platform with LangGraph"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/docusense.git
git push -u origin main
```

The first push opens a browser window asking you to sign in to GitHub. Approve it. Refresh the GitHub page and your code is there.

**Prefer clicking to typing?** VS Code's Source Control panel (the branch icon in the left sidebar) has a **Publish to GitHub** button that does all of the above in one step. It still respects `.gitignore`.

**Future changes:** after editing code, run
```bash
git add .
git commit -m "Describe what you changed"
git push
```
Render and Vercel redeploy automatically on every push.

### 1.4 Make the repo look good

- On the repo page, click the gear next to **About** and add a description ("Multi-tenant agentic RAG platform: LangGraph, FastAPI, Qdrant, Next.js") and topics: `rag`, `langgraph`, `fastapi`, `qdrant`, `llm`, `nextjs`.
- After Part 3, put your live URL in the About box's website field.
- GitHub renders the agent diagram in the README automatically, so visitors see the LangGraph flow on the front page.

---

## Part 2 — Create the free service accounts

The free deployment swaps each container from `docker-compose.yml` for a free managed service. The code supports both setups through environment variables, so nothing in the code changes.

| Piece | Local (docker compose) | Free deployment | Free-tier limits that matter |
|---|---|---|---|
| Frontend | Next.js container | **Vercel** Hobby | Personal, non-commercial use only. A portfolio demo is fine. |
| Backend API | FastAPI container | **Render** free web service | 512 MB RAM, 0.1 CPU. Sleeps after 15 min idle; the first request then takes about a minute. |
| Ingestion worker | Celery container | Runs inside the API (`INGEST_MODE=inline`) | Render's free tier has no background workers. |
| Postgres | Postgres container | **Neon** | 0.5 GB per project. Scales to zero when idle. |
| Vectors | Qdrant container | **Qdrant Cloud** free cluster | 1 GB RAM. Suspended after 1 week unused, deleted after 4 weeks unused. |
| Cache (optional) | Redis container | **Upstash** Redis | 256 MB, 500k commands per month. |
| LLM | Ollama on your GPU | **Groq** API (`llama-3.1-8b-instant`) | 30 requests/min, 6,000 tokens/min, 500k tokens/day. |

**About the LLM swap.** The free demo calls Llama 3.1 8B on Groq, so it runs the same model family as your self-hosted setup without needing a GPU. Groq is a third-party API, so the public demo does not keep data on your own infrastructure. That's fine for sample documents. The private deployment keeps `LLM_PROVIDER=ollama`, and that's what you pitch to companies. Say this plainly if anyone asks; it shows you understand the trade-off.

**Why not Hugging Face Spaces?** Docker Spaces now require a paid PRO plan to create, so they aren't a free option anymore.

Create each account and save the values listed under **Copy** into a scratch note. You'll paste them into Render in Part 3. Treat them as passwords.

### 2.1 Neon (Postgres)

1. Sign up at https://neon.com (GitHub login works).
2. Create a project named `docusense` in the region closest to you.
3. On the dashboard, click **Connect** and copy the connection string.
   **Copy:** `DATABASE_URL` — it looks like `postgresql://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`. Paste it exactly as shown; the backend handles the `sslmode` part.

### 2.2 Qdrant Cloud (vector database)

1. Sign up at https://cloud.qdrant.io.
2. **Clusters → Create cluster → Free**. Pick the same cloud region as Neon if you can.
3. When it's ready, open the cluster and copy its endpoint URL.
   **Copy:** `QDRANT_URL` — like `https://xxxx.eu-central-1-0.aws.cloud.qdrant.io:6333`
4. Go to **API Keys → Create** for that cluster.
   **Copy:** `QDRANT_API_KEY`

The app creates its collection and indexes on first use, so there's nothing else to set up.

### 2.3 Groq (LLM)

1. Sign up at https://console.groq.com.
2. **API Keys → Create API Key**. Groq shows the key only once.
   **Copy:** `LLM_API_KEY` — starts with `gsk_`

### 2.4 Upstash (optional semantic cache)

Skip this and the app still works; repeated questions just won't be served from cache.

1. Sign up at https://upstash.com → **Create Database** (Redis), free plan.
2. On the database page, find the **TCP / redis-cli** connection URL (starts with `rediss://`).
   **Copy:** `REDIS_URL`

---

## Part 3 — Deploy

### 3.1 Backend on Render

1. Sign up at https://render.com with your GitHub account.
2. Click **New → Blueprint** and select your `docusense` repository. Render reads `render.yaml` from the repo and proposes a free web service named `docusense-api`.
3. Render asks for the values marked secret. Paste them in:

   | Variable | Value |
   |---|---|
   | `LLM_API_KEY` | your Groq key |
   | `DATABASE_URL` | your Neon connection string |
   | `QDRANT_URL` | your Qdrant cluster URL |
   | `QDRANT_API_KEY` | your Qdrant API key |
   | `REDIS_URL` | your Upstash URL, or leave empty |
   | `CORS_ORIGINS` | `*` for now (you'll lock it down in 3.3) |

   The blueprint already sets `SECRET_KEY` (randomly generated), `INGEST_MODE=inline`, `LLM_PROVIDER=openai`, the Groq base URL and the model name.
4. Click **Apply**. The first build takes 5 to 10 minutes because it downloads the embedding model into the image.
5. When it shows **Live**, copy the service URL, for example `https://docusense-api.onrender.com`.
6. Check it in your browser:
   - `https://docusense-api.onrender.com/health` should show `{"status":"ok"}`
   - `https://docusense-api.onrender.com/docs` shows the interactive API docs

**Fit the Groq free tier.** In Render → your service → **Environment**, add `RETRIEVAL_TOP_K=5`. Each question makes several LLM calls (route, grade, answer), and smaller grading batches keep each one under Groq's 6,000 tokens-per-minute limit. If you still see slow answers, that's the backend backing off from a rate limit and retrying, as designed.

### 3.2 Frontend on Vercel

1. Sign up at https://vercel.com with GitHub.
2. **Add New → Project**, then import the `docusense` repository.
3. Set **Root Directory** to `frontend`. Vercel detects Next.js.
4. Under **Environment Variables**, add:
   `NEXT_PUBLIC_API_URL` = `https://docusense-api.onrender.com` (your Render URL, with no trailing slash and no `/api`)
5. Click **Deploy**. In about two minutes you get a URL like `https://docusense-yourname.vercel.app`.

`NEXT_PUBLIC_` variables are baked in when the frontend builds. If you change the value later, redeploy from Vercel's **Deployments** tab so the new value takes effect.

### 3.3 Lock down CORS

Back in Render → **Environment**, set `CORS_ORIGINS` to your Vercel URL, for example `https://docusense-yourname.vercel.app`. Render redeploys automatically. Now only your frontend can call the API from a browser.

### 3.4 Try it

1. Open your Vercel URL and click **Create your workspace**. The first account in a workspace becomes its admin.
2. On **Documents**, upload two small files you're happy to make public (for example two short contracts or policy PDFs).
3. Wait for both to show **indexed**. On the free CPU this takes from a few seconds to a minute or two per file.
4. On **Chat**, ask a question about one file, then a comparison across both. Open **Agent steps** under the answer to see the route the graph took.
5. Open **Dashboard** to see the query metrics.

---

## Living with the free tier

**The first visit after a quiet spell is slow.** Render puts the API to sleep after 15 minutes without traffic and waking it takes about a minute, so the login page may hang briefly. Before a demo or interview, open `/health` a minute ahead to wake it up. If you want it always awake, Render's Starter plan costs $7/month.

**Uploaded files don't persist on Render's free disk.** That's fine here: each file is parsed and indexed into Qdrant and Postgres right after upload, and those keep the data. Only the original file is lost on restart. Keep demo uploads small, because 0.1 CPU makes embedding large PDFs slow.

**Keep Qdrant alive.** A free Qdrant cluster is suspended after a week with no use and deleted after four weeks. Ask the demo a question at least once a week, or reactivate the cluster from the Qdrant dashboard when it's suspended.

**Scanned PDFs fail on purpose.** Image-only PDFs have no text layer and OCR isn't enabled, so they show **failed** with that reason. Use PDFs with selectable text.

---

## Troubleshooting

| What you see | Likely cause and fix |
|---|---|
| Render build fails while downloading the model | Usually a temporary network failure. Click **Manual Deploy → Deploy latest commit**. |
| Render shows "Out of memory" or keeps restarting | 512 MB is tight. Upload smaller files, or move the backend to Render Starter ($7/month, same RAM but no sleep) or a host with more memory. |
| Frontend loads but sign-in says "Failed to fetch" | `NEXT_PUBLIC_API_URL` is wrong, or `CORS_ORIGINS` doesn't exactly match your Vercel URL (check `https` and no trailing slash). Redeploy Vercel after fixing the variable. |
| Document stuck on **processing** | The API probably restarted mid-ingest (for example a free-tier sleep). Delete the document and upload it again. |
| Answers fail with a 429 error | Groq's free rate limit. Wait a minute, or lower `RETRIEVAL_TOP_K` to 4. |
| Errors mentioning `ssl` or `sslmode` | Make sure `DATABASE_URL` is Neon's string exactly as copied. |

To read the server's error messages, open your service in Render and click **Logs**.

---

## Alternative: the whole docker-compose stack on one free VM

To run the exact local architecture (Celery worker, self-hosted Qdrant and Redis, even Ollama with a small model) in the cloud, use a free virtual machine. Oracle Cloud's Always Free tier is the usual choice: it needs a credit card for identity verification, and its ARM machine allowance has been reported as up to 4 CPUs/24 GB, though some 2026 reports say new accounts now get 2 CPUs/12 GB, so check Oracle's current Free Tier page. On such a VM you install Docker, clone your repo, create `.env` and run `docker compose up -d`, exactly as on your laptop. It's more setup and you maintain the server yourself, so do this after the Render + Vercel version works.
