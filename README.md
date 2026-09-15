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
so it survives across runs. Puter is retired — there is no `~/.puter_token`, so
that bridge could only ever fail.

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

Three places, all in `bin/ai_gateway.py` unless noted:

1. `PROVIDERS` — the OpenAI-compatible chat URL.
2. `FALLBACK_MODEL` — a model id it serves (a wrong id 404s and falls through).
3. `AUTO_CHAIN` — where it sits in the fallback order.
4. `bin/vault-env` — the `<PROVIDER>_API_KEY` mapping so `keys on` exports it.

A provider with no key is inert, so adding one early is harmless.
