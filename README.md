# ~/bin — the AI toolbelt

One config, one endpoint, one interface. This file exists so the stack does not
have to be rediscovered every session. If something here is wrong, fix this
file in the same commit as the code.

---

## The shape of it

```
  ai / errfix / anchor / aider / vibe / ox
                    │
                    ▼
        bin/ai_lib.py  (one client, one call shape)
                    │
                    ▼
   bin/ai_gateway.py  ──►  127.0.0.1:7071   (OpenAI-compatible)
                    │
                    ▼
     provider key pools, read from ~/.concierge/vault.json
```

- **One config:** `~/.config/ai/config.toml`
- **One endpoint:** `http://127.0.0.1:7071` (gateway root; CLIs want
  `http://127.0.0.1:7071/v1/auto`)
- **One place keys live:** `~/.concierge/vault.json` — never in a config file,
  never in the parent shell.

Before this, five CLIs each had their own config and their own single key, so a
dead key was invisible until that one tool failed.

---

## Commands

### `ai` — talk to any model

```bash
ai "explain this error"              # headless
ai                                    # interactive
ai --json "..."                       # machine-readable, for other agents
ai --model gemini "..."               # preset from config (see [models])
ai --model deepinfra::meta-llama/...  # pin provider::model
cat log | ai --stdin "what failed"    # read stdin
ai --models                           # what the gateway advertises
ai --health                           # pool + cooldown state (cheap)
ai --providers                        # live probe: live / dead / rate-limited
```

### `errfix` / `fix` / `trace` — tracebacks fix themselves

```bash
errfix python thing.py     # run it; if it fails, diagnose inline
errfix --apply python x.py # same, but write the fix if unambiguous
python x.py 2>&1 | fix --stdin
trace npm test             # auto-diagnose ONLY on failure
```

`--apply` is strict on purpose: it writes only when the suggested OLD block
matches the target file **exactly once**, and backs up first. Ambiguous patches
are printed, never applied.

### `ai_trace` — what actually happened on every call

```bash
python3 bin/ai_trace.py status             # capture mode + store location
python3 bin/ai_trace.py summary --days 7   # per-provider tokens, latency, cost
python3 bin/ai_trace.py redact < log.txt   # show what redaction does (stores nothing)
```

One JSONL record per gateway call in `~/.local/state/ai/traces/YYYY-MM-DD.jsonl`:
which provider **actually answered** (not which was requested), model, status,
latency, attempts, token counts, estimated cost, and a non-reversible key
fingerprint.

**Redaction runs on the way to disk, inside `record()`.** No code path writes raw
prompt text. Prompt text here is untrusted input that routinely carries paths,
emails and customer data, so the store is treated as a place personal data must
not reach — not as a place to clean up afterwards. A trace already on disk cannot
be un-leaked.

Cost is honest by default: only free tiers price at `0.0`; anything else reports
`unknown` rather than a guessed number. Add real rates under `[prices]` in
`~/.config/ai/config.toml`.

Traces deliberately do **not** reuse the gateway's `_tag()`, which reveals the
first and last four characters of a key. Traces use a sha256 prefix instead.

**STATUS: wired and recording.** Verified 2026-09-15: `ai_trace.py status`
reports `records: 5` against the live store, and `ai --spend` prints the
per-provider table — the same check caught a `swarms` call at 3687 ms, which
matches a direct gateway measurement to the millisecond.

This section previously claimed the opposite ("store is empty and `ai --spend`
does not exist"). The correction is recorded rather than quietly dropped: a
stale *not yet wired* note is how working tooling gets rebuilt by whoever reads
it next.

### `anchor` — persistent-state agent

```bash
anchor "what is wrong with this"      # ask
anchor --remember project '{"name":"x"}'
anchor --recall project
anchor --facts
anchor --stats
anchor --web "python sqlite3 upsert"  # keyless web search
cat f.py | anchor "review this"
```

State lives in SQLite at `~/.anchor/anchor.db` (messages, facts, tool_runs),
so it survives across runs.

Two corrections to this section (2026-09-15):

- **Puter is not retired.** There is still no `~/.puter_token`, but three Puter
auth tokens now live in the vault as the `puter` pool, and the gateway routes to
them. The endpoint is `https://api.puter.com/puterai/openai/v1/chat/completions`
with plain Bearer auth; the account currently answers `402 subscription
required`.
- **anchor no longer dies on bare invocation.** It used to `IndexError` on line
one whenever stdin was not a TTY — i.e. under a pipe, cron, or another agent,
which is exactly the headless case it exists for. In a terminal it printed usage
and looked fine, which is why the crash stayed hidden.

### `keys` — the vault, one command

```bash
keys          # current mode + what is loaded
keys on       # load live keys into THIS shell
keys off      # scrub them back out
keys list     # providers in the vault
keys get <p>  # one provider's key
```

### Gateway lifecycle

```bash
systemctl --user status ai-gateway     # is it up
systemctl --user restart ai-gateway    # after editing the vault or the code
bin/gateway-ensure                     # start on demand (idempotent, non-fatal)
bin/ai --providers                     # what is actually usable right now
```

The unit is enabled so rotation survives a reboot. `gateway-ensure` also starts
it on demand, so nothing breaks if the unit is ever stopped.

---

## Adding or replacing a key

```bash
python3 ~/valet_concierge_system/concierge.py add deepinfra <KEY>
systemctl --user restart ai-gateway     # the gateway reads the vault at call time
bin/ai --providers                      # confirm it is live, not just present
```

For a provider you want to hold several keys of, put them in
`keys_by_provider.<provider>` as a JSON list; the gateway round-robins them and
cools individual keys on 429.

**Then verify, always.** A key that lists fine can still be revoked (403), out
of credit, or missing a subscription — `ai --providers` sends a 1-token chat
specifically because `/models` reports 200 for all three of those cases.

---

## How the gateway routes

- `/v1/auto/chat/completions` walks `AUTO_CHAIN` (paid providers first) and
  falls through on failure.
- `/v1/<provider>/chat/completions` pins one provider.
- A provider with **no keys is skipped**, so the chain is safe to list
  optimistically.
- Per key: `429` → 30 s cooldown. `429` carrying `insufficient_quota`, plus
  `401/402/403` → quarantine, because those do not recover in 30 seconds.
- A provider whose **whole pool** is auth-failed is marked dead and skipped,
  instead of burning the attempt budget on every request.
- A provider that accepts a request and never answers is bounded by a
  per-attempt timeout.

### Every vault pool has a route (2026-09-15)

The vault held **14 pools** while `PROVIDERS` routed **8**, so six pools' keys
arrived and were never read. All six are now wired with URLs verified live —
each returned `200` from `GET /v1/models`, which proves the host *and* the key:

| pool | state when wired |
|---|---|
| **swarms** | **live and free** — fronts 1908 model ids, 2.30–4.7 s, now second in `AUTO_CHAIN` |
| bazaar | endpoint OK (`bazaarlink.ai`, not `bazaar.ai`) — 402, no credits |
| orcarouter | endpoint OK — 429, free models need a linked GitHub account |
| zenmux | endpoint OK — 403, key has no `payg` entitlement |
| sageox | endpoint OK — 502, upstream temporarily unavailable (retryable) |
| puter | endpoint OK — 402, subscription required |

Two pools need no route because they are duplicates: `vibe` is **byte-identical**
to `mistral`, and `jules` is a Google-key pool that was a superset of `gemini` —
three of its usable keys were merged into `gemini`, taking that pool from **3
keys to 6**. Evidence for every row: `docs/PROVIDER_ENDPOINTS.md`.

---

## Known limits

- Free tiers rate-limit under bursts; rotation mitigates that, it cannot
  manufacture quota. `ai --providers` is the truth-teller — read it before
  blaming a tool.
- `gemini-2.5-*` is refused for these keys (Google: "no longer available to new
  users") and `gemini-3.5-flash` accepts a request then never answers. Use the
  `gemini-flash-latest` alias.
- Long generations can exceed the 60 s upstream timeout; pass a smaller ask.

## Adding a provider

Four places, all in `bin/ai_gateway.py` unless noted:

1. `PROVIDERS` — the OpenAI-compatible chat URL.
2. `FALLBACK_MODEL` — a model id it serves (a wrong id 404s and falls through).
3. `AUTO_CHAIN` — where it sits in the fallback order.
4. `bin/vault-env` — the `<PROVIDER>_API_KEY` mapping so `keys on` exports it.

A provider with no key is inert, so adding one early is harmless.

---

## Elsewhere in the stack

- `~/projects/freebuff-lean/` — a **25 MB** line-oriented client over this same
  gateway, measured against the shipping Freebuff client's ~436–566 MB and
  45–80% CPU on this machine. See its `WHO_DID_WHAT.md` for the numbers.
- `~/NEXT_STEPS.md` — the live work queue plus verified findings, so a future
  session does not re-derive them.
- `~/docs/PROVIDER_ENDPOINTS.md` — where to get keys and what each provider needs.
- Ledgers for this directory: `bin/WHO_DID_WHAT.md`.

## Vault tooling and the commit gate (2026-10-25)

Four scripts are the operational truth for keys, and they live here, not in the
home repo (`~/bin` is its own git repo, `bin-scripts`, branch `master`):

| script | what it answers |
|---|---|
| `ingest_keys.py` | where does this pasted key belong? Dry-run unless `--write` |
| `verify_keys.py` | which vault keys actually work right now? `--apply` records it |
| `model_scout.py` | what models can these keys reach? Writes `RootBase/model_registry.json` |
| `usage_report.py` | is any of this actually used? Static, reference and runtime evidence, kept apart |

Rules worth not relearning:

- A key is only assigned when a provider is anchored to it. Ambiguous `sk-...`
  keys are reported, never guessed. A label that contradicts a unique key shape
  is a conflict, not an instruction.
- **A probe must be able to fail.** An endpoint qualifies only if it rejects
  both a missing and a deliberately bogus key. `sambanova`, `zenmux`, `bazaar`,
  `sageox` and `puter` cannot meet that bar today and are listed in `NO_PROBE`
  with the reason. `verify_keys.py` writes `probe: validated|unsupported` next
  to each status so the absence of a verdict is visible.
- Never print a key. `mask()` gives a 4/3 preview plus an 8-char sha256 prefix;
  fingerprints are how duplicates are detected (`cohere`, `elevenlabs`,
  `stability` and `aimlapi` all held DeepSeek's key, `jules` held Gemini's,
  `vibe` held Mistral's).

### Telemetry

`telemetry.py` appends one row per tool run to
`RootBase/logs/shared/bin_tools/events.tsv` (`stamp, tool, rc, dur_s,
bus_available, detail`). Deliberately **not** Thoth's `thoth_bus.tsv`: folding
tool runs into the bus-availability log would inflate a number that measures
something else. Coverage is honest — only tools that call `install()` (the four
above) or run through `toolrun` appear; absence reads as "not instrumented",
never as "never used".

### The gate

`git-ship` chains compile → secret scan → larp → tests over the **staged set**,
and is installed as `pre-commit` in `~/bin`, `~/grim-oracle` and
`~/turd-merger-tools`. `--full` runs the whole home suite; `--strict-larp` makes
advisory prose findings blocking.

`assertion-free-test` and `trivial-assertion` block. `claim-without-receipt`
does not: it fires on the word "verified" in any nearby comment, including true
ones, and a gate that blocks honest docstrings gets `--no-verify`'d. A bypassed
gate is worse than no gate.

### Vault debt closed (2026-10-25)

Five entries are `retired` rather than deleted, so the bytes survive and
nothing claims they work: `bazaar`, `sageox`, `zenmux`, `puter`, `unknown`.
The reason is recorded in each entry's `status_note`. Three of those domains
(`bazaar.ai`, `sageox.com`, `vibe.ai`) resolve to parking IPs — they are
for-sale domains, not API providers, which is why no probe could ever validate
them. `verify_keys.py` skips retired entries and says so.

Final state: 15 entries — 9 `live`, 1 `dead` (sambanova, 401), 5 `retired`.
