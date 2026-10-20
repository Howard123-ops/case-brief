"""Case Brief web app: serves docs/index.html and proxies AI requests to OpenRouter.

The OpenRouter key stays on this server (read from .env), so it never reaches the browser.
The browser can only ask for two things -- a brief of a named case, or a cartoon of a
brief's facts -- and the prompts are built here, so the endpoint can't be used as a
general-purpose free LLM.

Run:  python3 server.py        then open http://localhost:8787
"""
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "docs" / "index.html"
PORT = int(os.getenv("PORT", "8787"))
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_BODY = 20_000  # bytes; requests are just a case name or a few paragraphs of facts


def load_env():
    """Tiny .env reader so the server needs nothing beyond the standard library."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
API_KEY = (os.getenv("BRIEF_APP_OPENROUTER_KEY") or os.getenv("OPENROUTER_API_KEY") or "").strip()
MODEL = os.getenv("BRIEF_APP_MODEL", "openrouter/free")


BRIEF_PROMPT = """You are an experienced U.S. law professor helping a law student prepare for class.

TASK: Write a case brief of this case: {case}

FORMAT: Write ONLY the sections below. Start each with its marker on its own line, exactly as \
shown, in this order. Put no text before the first marker. Inside a section use plain \
paragraphs, "- " bullet lines, and **bold** only. No headings or tables.

[[CAPTION]]
Name: the full case name (e.g. "Hadley v. Baxendale")
Court: the deciding court
Year: year decided
Citation: the official citation in Bluebook form if you know it, otherwise "Unknown"
Judge: the author of the majority opinion, otherwise "Unknown"
[[FACTS]]
The legally relevant facts in 1-3 short paragraphs. Identify parties by name and role, \
e.g. "Hadley (plaintiff)".
[[PROCEDURAL_HISTORY]]
The claims, each lower court's ruling, and who appealed.
[[HOLDING]]
The question the court decided and its answer in one or two sentences, plus the disposition \
(affirmed, reversed, remanded) and the vote if known.
[[RATIONALE]]
The court's reasoning step by step, as 3-5 "- " bullets each starting with a short **bold** \
lead-in. Mention precedents or policy the court relied on.

If you cannot identify one specific case with confidence, write only this instead:
[[NOT_FOUND]]
One or two sentences on what is missing. If the name could match several cases, list the likely ones.

ACCURACY: Do not invent facts, citations, vote counts, or quotations. If you are unsure of a \
detail, write "Unknown" or say it is uncertain rather than guessing."""

CARTOON_PROMPT = """You are a courtroom cartoonist. Draw ONE cartoon, as a single SVG image, \
that shows what happened in this case so a law student remembers the facts.

CASE: {name}
FACTS:
{facts}

STYLE: A friendly, funny newspaper comic strip: bold black outlines (stroke-width 3-4), flat \
bright colors, and a colored scene background in every panel (sky, room, street, field, etc.) \
with recognizable props from the facts.

LAYOUT: Exactly 3 panels telling the story in order (before -> key event -> result). Panel \
frames are white-filled rounded rectangles with a 4px black stroke at exactly x=20, x=413 and \
x=806, each y=20, width=374, height=460. Everything in a panel must stay inside its frame.

CHARACTERS: Big, expressive cartoon people, each at least 180px tall: round head with eyes, \
eyebrows and mouth showing emotion, a filled body with clothes (not stick figures), arms and \
legs. Keep the same colors for the same person in every panel. Under each person put a name \
tag: white rounded rect with black stroke and bold text such as "Hadley (plaintiff)", font-size 18.

TEXT: At most one speech bubble per panel (white rounded rect or ellipse with a small tail, \
black stroke), under 7 words, font-size 20, wrapped into lines with <tspan> so it fits inside \
the bubble. Put a short caption strip at the top of each panel (font-size 18, bold), e.g. \
"1. The shaft breaks". No text may overlap other text or run outside its panel.

SVG RULES:
- Start with <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 500"> and end with </svg>.
- Fill the whole background first (a <rect> covering 0 0 1200 500).
- Use only basic shapes, paths, groups, gradients and <text> with font-family="Arial, Helvetica, sans-serif".
- No <script>, <foreignObject>, <image>, external links, or CSS animations.
- Keep it under 12,000 characters.

Output ONLY the SVG code: no explanation and no code fences."""


def build_prompt(req):
    kind = req.get("kind")
    if kind == "brief":
        case = str(req.get("case", "")).strip()[:300]
        if len(case) < 3:
            raise ValueError("Type a case name, like Hadley v. Baxendale.")
        return BRIEF_PROMPT.format(case=case)
    if kind == "cartoon":
        name = str(req.get("name", "")).strip()[:300]
        facts = str(req.get("facts", "")).strip()[:3000]
        if not facts:
            raise ValueError("No facts to draw.")
        return CARTOON_PROMPT.format(name=name, facts=facts)
    raise ValueError("Unknown request.")


def open_stream(prompt):
    """POST to OpenRouter with streaming. Free reasoning models can think for minutes before
    writing, so ask for no reasoning first; if a model insists on it, retry with low effort."""
    last = None
    for reasoning in ({"enabled": False}, {"effort": "low"}):
        body = json.dumps({
            "model": MODEL, "stream": True, "reasoning": reasoning,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "X-Title": "Case Brief",
        })
        try:
            return urllib.request.urlopen(req, timeout=240)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            last = e.code, detail
            if e.code == 400 and "reasoning" in detail.lower() and reasoning.get("enabled") is False:
                continue
            break
    return last  # (status, detail)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html", "/case-brief.html"):
            return self.send_json(404, {"error": {"message": "Not found"}})
        data = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/api/ai":
            return self.send_json(404, {"error": {"message": "Not found"}})
        if not API_KEY:
            return self.send_json(401, {"error": {"message": "No OpenRouter key on the server. Add BRIEF_APP_OPENROUTER_KEY to .env."}})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.send_json(413, {"error": {"message": "Request too large."}})
        try:
            prompt = build_prompt(json.loads(self.rfile.read(length) or b"{}"))
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": {"message": str(e)}})

        try:
            upstream = open_stream(prompt)
        except Exception as e:  # DNS, TLS, timeout
            return self.send_json(502, {"error": {"message": f"Couldn't reach OpenRouter: {e}"}})
        if isinstance(upstream, tuple):
            status, detail = upstream
            try:
                msg = json.loads(detail)["error"]["message"]
            except Exception:
                msg = detail[:300]
            return self.send_json(status, {"error": {"message": msg}})

        # Pipe OpenRouter's server-sent events straight through to the browser.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with upstream:
                while True:
                    chunk = upstream.read1(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the user pressed Stop or closed the tab

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}")


if __name__ == "__main__":
    if not API_KEY:
        print("Warning: no OpenRouter key found in .env; AI requests will fail.")
    print(f"Case Brief running at http://localhost:{PORT}  (model: {MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
