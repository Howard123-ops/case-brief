# Case Brief

Type a U.S. case name and get a brief (facts, procedural history, holding, rationale), with an
AI-drawn cartoon of the facts at the top. Uses free models on [OpenRouter](https://openrouter.ai).

The OpenRouter key never reaches the browser. The page asks a small backend for "brief this case" or
"draw these facts", and the backend builds the prompt and calls OpenRouter with the key.

| File | What it is |
|---|---|
| `docs/index.html` | The whole web page (served by GitHub Pages from `docs/`) |
| `worker/worker.js` | Backend for the hosted site: a Cloudflare Worker that holds the key |
| `server.py` | Backend for running locally: serves the page and does the same job as the Worker |

The prompts live in both `server.py` and `worker/worker.js`; keep them in sync.

## Run locally

```bash
cp .env.example .env   # then paste your OpenRouter key
python3 server.py      # open http://localhost:8787
```

Standard library only; no installs.

## Hosted version

1. **Worker.** In the Cloudflare dashboard, create a Worker, paste in `worker/worker.js`, and deploy.
   Under *Settings > Variables and Secrets* add a **secret** `OPENROUTER_KEY` with your key, and
   optionally `ALLOWED_ORIGINS` = `https://<username>.github.io`.
2. **Page.** Set `WORKER_URL` near the top of the script in `docs/index.html` to the Worker's URL
   (e.g. `https://case-brief.<subdomain>.workers.dev`).
3. **GitHub Pages.** Repo *Settings > Pages*: deploy from branch `main`, folder `/docs`.

AI output can be wrong, especially citations and procedural history. Check it against the opinion.
