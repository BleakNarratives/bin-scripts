# WHO_DID_WHAT — ~/bin

Append-only. Newest at the bottom. Heading format: `## DATE — who — scope`.

This directory is its own git repo (`BleakNarratives/bin-scripts`), separate from
the home repo, because the home repo ignores `/*`.

---

## 2026-09-15 — Agent (Buffy) — bin/ toolchain: gateway, one interface, error fixing, traces

### Fixed: the key rotation was never broken, it was blocked before auth
- **Root cause of "revoked" Groq and Cerebras keys: Cloudflare error 1010.** It
  bans Python-urllib's default User-Agent, so requests got HTTP 403 *before*
  reaching auth. Same dummy key: default UA -> 403 "error code: 1010"; browser UA
  -> 401 "Invalid API Key".
- Consequence: the gateway was quarantining whole provider pools as auth-dead on a
  false diagnosis. Six working keys had been purged weeks earlier for this reason.
  **All 11 Groq keys are live (HTTP 200)** after fixing the UA header.
- Also fixed: Cerebras serves only 3 models (`gpt-oss-120b`, `qwen-3.8-27b`,
  `gemma-4-31b`). The config asked for `llama-3.3-70b`, which 404s — so four valid
  keys looked broken.
- Also fixed: every HTTP 429 was treated as transient, so a quota-exhausted OpenAI
  account was retried forever on every request. `insufficient_quota` and
  `credit_balance_exhausted` are now distinguished from real rate limits.

### Added: one config, one interface
- `~/.config/ai/config.toml` — the single config (gateway URL, named presets,
  headless system prompt).
- `bin/ai_lib.py` — the one client every local tool calls. Holds no API key.
- `bin/ai` — headless, interactive, and `--json` for other agents. Plus
  `--models`, `--health`, `--providers`.
- `bin/ai_gateway.py` — the rotating OpenAI-compatible endpoint on
  `127.0.0.1:7071`. `route()` and `call_upstream()` now also report which provider
  actually answered, so a silent misroute is visible instead of looking identical
  to a working call.
- `.config/systemd/user/ai-gateway.service` — installed, deliberately **not**
  enabled for boot. `bin/gateway-ensure` starts it on demand. Note: a plain
  `nohup` start did not survive the shell; systemd does.
- `.aider.conf.yml` repointed at `/v1/auto`. Verified live: `REQ -> 200`.

### Added: honest provider dashboard
- `GET /providers` (root, not under `/v1`) probes real capability rather than
  trusting `/models`. `/models` returns 200 for the OpenAI account with zero
  credits and the Mistral account with no subscription, which is exactly the
  false-health problem this repo already suffers from.
- Current state: groq 11 live, openrouter 5 live, mistral 6 rate-limited,
  openai 3 unpaid, gemini 3 unpaid.

### Repaired: Anchor
- `bin/anchor/anchor.py` crashed on `import patcher` — no such module existed.
  Added `patcher.py`, `store.py` (SQLite at `~/.anchor/anchor.db`),
  `web_search.py` (keyless), and rewrote `memory.py` and `models.py`.
- Store/retrieve round-trip verified. Full run returns `ANCHOR-OK`.
- Note: the `anchor` previously on PATH was not Anchor at all — `RootBase/bin/anchor`
  is a symlink to `RootBase/bin/puter`.

### Added: errfix — tracebacks diagnose themselves
- `bin/errfix` reads a real traceback, pulls surrounding source, and proposes an
  exact patch. Diagnose is the default; `--apply` requires a unique match.
- Fixed a real prompt-contract bug found by testing: the model was copying the
  line-number gutter (`2 | `) out of the context it was fed, so every patch
  refused to apply. Verified end-to-end: applied a real fix, made a backup,
  script ran clean (exit 0).
- `trace` is wired into `.bash_aliases` so a failing command auto-diagnoses and a
  passing one passes through untouched.

### Added: bin/ai_trace.py — provenance trace store
- One JSONL record per gateway call: provider that actually answered, model,
  status, latency, attempts, token counts, estimated cost, and a **non-reversible**
  key fingerprint.
- **Redaction runs inside `record()`, on the way to disk.** There is no path that
  writes raw prompt text. Entering the corpus after the fact is not a safeguard —
  a trace already on disk cannot be un-leaked.
- Deliberately does NOT reuse the gateway's `_tag()`, which reveals the first and
  last four characters of a key. Traces use a sha256 prefix instead.
- Cost is honest by default: only free tiers are priced at `0.0`; anything else is
  `unknown` rather than a guessed number.
- **STATUS: written, but NOT yet wired into the gateway.** `ai --spend` does not
  exist yet. `/cost` in the lean client delegates to it and correctly reports the
  empty store.

### Keys and vaults — deliberately NOT removed
- `~/.concierge` vault, `valet`, and `concierge` are **load-bearing**. Files in
  `~/.local/bin` resolve through `bin/vault-launch`; removing them breaks aider,
  vibe, ox, sageox and codex.
- Added one memorable `keys` command that replaces `vload` / `vget` / `vlist` /
  `vault_toggle speakeasy`. The old names still work. `keys on` / `keys off`
  verified to load and scrub.

### Commits
`d1e0571`, `7fdc891` (toolchain + paid providers), `f3a1266`, `65a655a`
(User-Agent fix, Cerebras model ids, errfix prompt contract).

### Conventions established
- Providers prefer paid before free tiers **except** Groq, which went first
  because it had 11 live keys and is free.
- A 200 response carrying an error envelope (seen from OpenRouter) must fall
  through to the next provider, not end the chain.

---

## 2026-09-15 — Agent (Buffy) — vault↔gateway provider reconciliation, Puter, and provider docs

### The actual bug: 14 pools, 8 routes
- `keys_by_provider` held **14 pools**; `PROVIDERS` routed **8**. Six pools' keys
  arrived from the vault and **were never read by anything**. That is the whole
  "my keys don't work" complaint in one line — not bad keys, no route.
- All six are now wired in `PROVIDERS` + `FALLBACK_MODEL` + `AUTO_CHAIN`, with
  URLs verified against the real key in the vault. Every one returned `200` from
  `GET /v1/models`, which proves host **and** key are real.

### Live and free: swarms
- `https://api.swarms.world/v1/chat/completions` — real completion, **2.30 s**
  fastest of five ids tested, and it fronts **1,908 model ids**, including
  `gemini/gemini-3.1-pro-preview`. So there is now a route to Gemini models that
  does not depend on the Google key at all.
- Now **second in `AUTO_CHAIN`**, behind Groq. Verified end-to-end through the
  gateway: `swarms::cerebras/qwen-3-32b` -> `HTTP 200`, 3.74 s, and `auto` ->
  `HTTP 200`, 0.48 s served by `openai/gpt-oss-120b`.

### Wired but inert, each for an ACCOUNT reason (not a key or URL reason)
| pool | endpoint | result | unblock by |
|---|---|---|---|
| bazaar | `api.bazaarlink.ai/v1` | `402` no credits | topping up |
| orcarouter | `api.orcarouter.ai/v1` | `429` free_rate_limited | linking a GitHub account |
| zenmux | `zenmux.ai/api/v1` | `403` payg entitlement | account tier |
| sageox | `api.sageox.ai/v1` | `502` upstream down | retry |
| puter | `api.puter.com/puterai/openai/v1` | `402` subscription | subscribing |

- `puter` was added to `bin/vault-env`'s `mapping` too — it was the one pool with
  no env export, so `keys on` would have silently skipped it.

### Duplicates proven, not assumed
- **`vibe` = `mistral`**, all six keys byte-identical. Routing it separately
  would have round-robined the same six keys twice and corrupted per-key cooldown.
- **`jules` ⊇ `gemini`** (all 3 gemini keys sit inside its 8). Three of its five
  Google-format extras answered `200` and were merged into `gemini`:
  **3 keys -> 6**. `jules` left intact on purpose (separate Google product).

### Mistakes made and corrected in this same session — on the record
- **I dropped three live gemini keys.** My dedupe seeded `seen` with the existing
  pool, so every original key was skipped as "already seen" and the pool was
  rebuilt from only the two new ones (`3 -> 2`). Caught in the same output by the
  count going *down*. Restored from the backup taken immediately before the
  write, verified byte-identical, then redone starting *from* the existing list
  with `assert set(before) <= set(merged)` so it cannot recur.
- **I called `auto` broken after testing the wrong URL.** `/v1/chat/completions`
  is not the auto route; `/v1/auto/chat/completions` is. The gateway was fine.
- **A single probe attempt is not evidence.** One jules key read `429` on attempt
  one and `200` on attempt two. The merge now probes twice — a transient quota
  error and a revoked key look identical from one call.
- **`/v1/models` 200 does not mean usable.** bazaar, zenmux and orcarouter all
  listed hundreds of models and then refused every chat. Same class as the four
  Cerebras keys that looked revoked because of a wrong model id.
- **`bazaar` is not at `bazaar.ai`.** `sk-bl-` keys belong to **BazaarLink**;
  `api.bazaarlink.ai` is the host. The provider key stays `bazaar` only because
  it must match the vault pool name.

### Docs corrected (both claims were false, verified before editing)
- `bin/README.md` said the trace store was empty and `ai --spend` did not exist.
  Actual: `records: 5`, and `ai --spend` prints the per-provider table — it caught
  the swarms call at 3687 ms. The stale *not yet wired* note is now replaced with
  a correction and a one-line reason, not silently deleted.
- `bin/README.md` said "Puter is retired." Actual: three Puter tokens in the
  vault, endpoint wired, Bearer auth verified live (account returns `402`).
- `docs/PROVIDER_ENDPOINTS.md` gained a real-key section, the duplicates, the
  "probe twice" rule, and the `/v1/models` false-positive warning. Its gemini row
  now reads *throttled, not dead — 6 keys*.

### Verified numbers
- vault pools **14** / routed **12** / unrouted **2** (both proven duplicates)
- chain entries with no default model: **none**
- gateway: `active`; `ai_gateway.py` compiles

### Open / next
- Move `puter`, `bazaar`, `orcarouter`, `zenmux`, `sageox` up `AUTO_CHAIN` as each
  account unblocks — they are parked at the tail so they do not add a
  guaranteed-failing hop to every `auto` request.
- `keyctl` (ingest -> validate -> quarantine dead -> report) is still unbuilt.
  `keys` is an interactive-only shell **function**, so nothing headless can drive
  it; a real command is the prerequisite for any scheduled key lifecycle.
