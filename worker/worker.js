// Case Brief: Cloudflare Worker that does what server.py does locally, for the GitHub Pages site.
// It holds the OpenRouter key as a secret (OPENROUTER_KEY), builds the prompts itself, and only
// answers "brief this case" and "draw these facts" requests, so the key never reaches the browser.
// Keep the prompts in sync with server.py.
//
// Settings (Worker > Settings > Variables and Secrets):
//   OPENROUTER_KEY   (secret, required)  your OpenRouter key
//   ALLOWED_ORIGINS  (text, optional)    comma-separated origins allowed to call this, e.g.
//                                        https://yourname.github.io ; defaults to any origin
//   MODEL            (text, optional)    defaults to openrouter/free

const API_URL = "https://openrouter.ai/api/v1/chat/completions";
const MAX_BODY = 20000;

const BRIEF_PROMPT = (c) => `You are an experienced U.S. law professor helping a law student prepare for class.

TASK: Write a case brief of this case: ${c}

FORMAT: Write ONLY the sections below. Start each with its marker on its own line, exactly as shown, in this order. Put no text before the first marker. Inside a section use plain paragraphs, "- " bullet lines, and **bold** only. No headings or tables.

[[CAPTION]]
Name: the full case name (e.g. "Hadley v. Baxendale")
Court: the deciding court
Year: year decided
Citation: the official citation in Bluebook form if you know it, otherwise "Unknown"
Judge: the author of the majority opinion, otherwise "Unknown"
[[FACTS]]
The legally relevant facts in 1-3 short paragraphs. Identify parties by name and role, e.g. "Hadley (plaintiff)".
[[PROCEDURAL_HISTORY]]
The claims, each lower court's ruling, and who appealed.
[[HOLDING]]
The question the court decided and its answer in one or two sentences, plus the disposition (affirmed, reversed, remanded) and the vote if known.
[[RATIONALE]]
The court's reasoning step by step, as 3-5 "- " bullets each starting with a short **bold** lead-in. Mention precedents or policy the court relied on.

If you cannot identify one specific case with confidence, write only this instead:
[[NOT_FOUND]]
One or two sentences on what is missing. If the name could match several cases, list the likely ones.

ACCURACY: Do not invent facts, citations, vote counts, or quotations. If you are unsure of a detail, write "Unknown" or say it is uncertain rather than guessing.`;

const CARTOON_PROMPT = (name, facts) => `You are a courtroom cartoonist. Draw ONE cartoon, as a single SVG image, that shows what happened in this case so a law student remembers the facts.

CASE: ${name}
FACTS:
${facts}

STYLE: A friendly, funny newspaper comic strip: bold black outlines (stroke-width 3-4), flat bright colors, and a colored scene background in every panel (sky, room, street, field, etc.) with recognizable props from the facts.

LAYOUT: Exactly 3 panels telling the story in order (before -> key event -> result). Panel frames are white-filled rounded rectangles with a 4px black stroke at exactly x=20, x=413 and x=806, each y=20, width=374, height=460. Everything in a panel must stay inside its frame.

CHARACTERS: Big, expressive cartoon people, each at least 180px tall: round head with eyes, eyebrows and mouth showing emotion, a filled body with clothes (not stick figures), arms and legs. Keep the same colors for the same person in every panel. Under each person put a name tag: white rounded rect with black stroke and bold text such as "Hadley (plaintiff)", font-size 18.

TEXT: At most one speech bubble per panel (white rounded rect or ellipse with a small tail, black stroke), under 7 words, font-size 20, wrapped into lines with <tspan> so it fits inside the bubble. Put a short caption strip at the top of each panel (font-size 18, bold), e.g. "1. The shaft breaks". No text may overlap other text or run outside its panel.

SVG RULES:
- Start with <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 500"> and end with </svg>.
- Fill the whole background first (a <rect> covering 0 0 1200 500).
- Use only basic shapes, paths, groups, gradients and <text> with font-family="Arial, Helvetica, sans-serif".
- No <script>, <foreignObject>, <image>, external links, or CSS animations.
- Keep it under 12,000 characters.

Output ONLY the SVG code: no explanation and no code fences.`;

function buildPrompt(req) {
  if (req.kind === "brief") {
    const c = String(req.case || "").trim().slice(0, 300);
    if (c.length < 3) throw new Error("Type a case name, like Hadley v. Baxendale.");
    return BRIEF_PROMPT(c);
  }
  if (req.kind === "cartoon") {
    const facts = String(req.facts || "").trim().slice(0, 3000);
    if (!facts) throw new Error("No facts to draw.");
    return CARTOON_PROMPT(String(req.name || "").trim().slice(0, 300), facts);
  }
  throw new Error("Unknown request.");
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "*").split(",").map((s) => s.trim()).filter(Boolean);
    const okOrigin = allowed.includes("*") || allowed.includes(origin);
    const cors = {
      "Access-Control-Allow-Origin": allowed.includes("*") ? "*" : okOrigin ? origin : allowed[0],
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    const json = (status, message) => new Response(JSON.stringify({ error: { message } }), {
      status, headers: { ...cors, "Content-Type": "application/json" },
    });

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST" || new URL(request.url).pathname !== "/api/ai") return json(404, "Not found");
    if (!okOrigin) return json(403, "This site isn't allowed to use the Case Brief API.");
    if (!env.OPENROUTER_KEY) return json(401, "No OpenRouter key on the server. Add the OPENROUTER_KEY secret to the Worker.");

    let prompt;
    try {
      const raw = await request.text();
      if (raw.length > MAX_BODY) return json(413, "Request too large.");
      prompt = buildPrompt(JSON.parse(raw || "{}"));
    } catch (e) {
      return json(400, e.message || "Bad request.");
    }

    // Free reasoning models can think for minutes before writing, so ask for no reasoning first;
    // if a model insists on it, retry with low effort.
    let res;
    for (const reasoning of [{ enabled: false }, { effort: "low" }]) {
      try {
        res = await fetch(API_URL, {
          method: "POST",
          headers: { "Authorization": `Bearer ${env.OPENROUTER_KEY}`, "Content-Type": "application/json", "X-Title": "Case Brief" },
          body: JSON.stringify({ model: env.MODEL || "openrouter/free", stream: true, reasoning, messages: [{ role: "user", content: prompt }] }),
        });
      } catch (e) {
        return json(502, `Couldn't reach OpenRouter: ${e.message}`);
      }
      if (res.ok) break;
      const detail = await res.text();
      let msg = detail.slice(0, 300);
      try { msg = JSON.parse(detail).error.message || msg; } catch {}
      if (res.status === 400 && /reasoning/i.test(detail) && reasoning.enabled === false) continue;
      return json(res.status, msg);
    }

    // Pipe OpenRouter's server-sent events straight through to the browser.
    return new Response(res.body, {
      headers: { ...cors, "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
    });
  },
};
