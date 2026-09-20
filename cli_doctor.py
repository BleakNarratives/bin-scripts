#!/usr/bin/env python3

# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: os,shutil,subprocess
# ROLE: Read-only health report for canonical AI CLI stack
# AUTHOR: Bleak
# SESSION: 2026-09-11
# TIER: 4
# [/DNA_TAG]

"""Read-only health report for the canonical AI CLI stack.

No CLI is invoked, no network is contacted, no vault environment is loaded,
and no files are written. Credential values are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

CLIS = {
    "codex": {"env": ("OPENAI_API_KEY",), "vault": ("openai",), "configs": (".codex/config.toml",), "defaults": ("CLI-managed/OAuth", "OpenAI")},
    "aider": {"env": ("GROQ_API_KEY", "OPENROUTER_API_KEY"), "vault": ("groq", "openrouter"), "configs": (".aider.conf.yml",)},
    "vibe": {"env": ("GROQ_API_KEY", "MISTRAL_API_KEY"), "vault": ("groq", "mistral"), "configs": (".vibe/config.toml",)},
    "puter": {"env": ("GROQ_API_KEY", "OPENROUTER_API_KEY"), "vault": ("groq", "openrouter"), "configs": (), "defaults": ("openai/gpt-oss-120b", "groq")},
    "anchor": {"env": ("GROQ_API_KEY", "OPENROUTER_API_KEY"), "vault": ("groq", "openrouter"), "configs": (), "defaults": ("openai/gpt-oss-120b", "groq")},
    "ox": {"env": ("GROQ_API_KEY", "OPENROUTER_API_KEY"), "vault": ("groq", "openrouter"), "configs": (".oxrc",)},
    "sageox": {"env": ("SAGEOX_TOKEN", "SAGEOX_API_KEY"), "vault": ("sageox",), "configs": (), "defaults": ("team-context/adapter-managed", "SageOx")},
    "gemini": {"env": ("GEMINI_API_KEY",), "vault": ("gemini",), "configs": (), "defaults": ("CLI-managed", "Gemini")},
    # Kilo resolves {env:...} itself from .config/kilo/kilo.jsonc, so none of
    # these appear in `env` elsewhere. Listed here to mirror that config.
    "kilo": {
        "env": ("ZENMUX_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY", "CEREBRAS_API_KEY", "OPENAI_API_KEY", "BAZAAR_API_KEY"),
        "vault": ("zenmux", "openrouter", "groq", "anthropic", "gemini", "cerebras", "openai", "bazaar"),
        "configs": (".config/kilo/kilo.jsonc", ".kilo/kilo.jsonc"),
        "defaults": ("zenmux/z-ai/glm-5.2-free", "kilo"),
    },
    # Added 2026-09-15. These were absent from the report entirely, which is
    # part of why a dead 9router went unnoticed: nothing was looking at them.
    "grok": {"env": (), "vault": (), "configs": (".grok/config.toml",)},
    "opencode": {"env": (), "vault": (), "configs": (".config/opencode/opencode.json",)},
    "cmd": {"env": (), "vault": (), "configs": ()},
    "copilot": {"env": (), "vault": (), "configs": ()},
}
CANONICAL_PATHS = {
    "codex": Path.home() / "RootBase/bin/codex",
    "aider": Path.home() / ".local/bin/aider",
    "vibe": Path.home() / ".local/bin/vibe",
    "puter": Path.home() / "RootBase/bin/puter",
    "anchor": Path.home() / "RootBase/bin/anchor",
    "ox": Path.home() / "bin/ox",
    "sageox": Path.home() / ".local/bin/sageox",
    "gemini": Path.home() / ".npm-global/bin/gemini",
    "kilo": Path.home() / ".npm-global/bin/kilo",
    "grok": Path.home() / ".local/bin/grok",
    "opencode": Path.home() / ".npm-global/bin/opencode",
    "cmd": Path.home() / ".npm-global/bin/cmd",
    "copilot": Path.home() / ".npm-global/bin/copilot",
}
CONVENTIONAL_BIN_DIRS = tuple(path.parent for path in CANONICAL_PATHS.values()) + (Path("/usr/local/bin"), Path("/usr/bin"))
VAULT_JSON = Path.home() / ".concierge/vault.json"
VIRTUAL_KEY_VALUES = {"", "PLACEHOLDER", "sk-test_key_placeholder"}
SECRET_RE = re.compile(r"(api[_-]?key|token|secret|password)\s*[:=]", re.I)
MODEL_RE = re.compile(r"(?:model|active_model)\s*[:=]\s*[\"']?([^\"'\s#]+)", re.I)
PROVIDER_RE = re.compile(r"(?:provider|active_provider)\s*[:=]\s*[\"']?([^\"'\s#]+)", re.I)

# Provider -> env var name, mirroring the mapping in bin/vault-env so the two
# agree. Used when a vault key entry carries no explicit env_var field.
VAULT_ENV_FALLBACK = {
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "zenmux": "ZENMUX_API_KEY",
    "orcarouter": "ORCAROUTER_API_KEY",
    "bazaar": "BAZAAR_API_KEY",
    "openai": "OPENAI_API_KEY",
    "jules": "JULES_API_KEY",
    "swarms": "SWARMS_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "vibe": "VIBE_API_KEY",
    "sageox": "SAGEOX_API_KEY",
}
# Extra names bin/vault-env exports as aliases inside a vault-loaded process.
VAULT_ENV_ALIASES = ("ZAI_API_KEY", "SAGEOX_TOKEN")

ENV_PLACEHOLDER_RE = re.compile(r"\{env:([A-Za-z0-9_]+)\}")
ENV_REF_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]{2,})\}?")
LOOKS_SECRET_RE = re.compile(r"(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$")

# Bounded discovery. The walk is read-only and depth/file-capped so a doctor
# run stays predictable on a home directory this size.
SCAN_ROOTS = (
    Path.home() / ".config",
    Path.home() / ".kilo",
    Path.home() / ".vibe",
    Path.home() / ".codex",
    Path.home() / ".claude",
    Path.home() / "bin",
    Path.home() / "MikeySwarm",
    Path.home() / "RootBase",
)
SCAN_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".cache", "venv", ".venv",
                  "site-packages", ".rustup", ".cargo", "models"}
SCAN_SUFFIXES = {".json", ".jsonc", ".toml", ".yaml", ".yml", ".env", ".conf",
                 ".cfg", ".ini", ".service", ".timer"}
SCAN_MAX_DEPTH = 3
SCAN_MAX_FILES = 2000


def unique_paths(paths: Iterable[Path]) -> List[Path]:
    result: List[Path] = []
    seen = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def path_entries(name: str) -> List[str]:
    directories = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    directories.extend(CONVENTIONAL_BIN_DIRS)
    entries = []
    for directory in unique_paths(directories):
        candidate = directory / name
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                entries.append(str(candidate.resolve()))
        except OSError:
            pass
    return list(dict.fromkeys(entries))


def target_for(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if path.is_symlink() and resolved.name == "vault-launch":
        return f"vault-launch wrapper -> {resolved}"
    if path.is_symlink():
        return f"symlink -> {resolved}"
    try:
        text = path.read_text(errors="replace")[:12000]
    except OSError:
        return str(resolved)
    if "vault-launch" in text:
        return f"vault-launch wrapper script -> {resolved}"
    if "vault-env" in text and "exec python" in text:
        return f"inline vault wrapper -> {resolved}"
    return str(resolved)


def vault_data() -> Dict[str, object]:
    if not VAULT_JSON.is_file():
        return {"error": "vault missing"}
    try:
        value = json.loads(VAULT_JSON.read_text())
        return value if isinstance(value, dict) else {"error": "invalid vault root"}
    except (OSError, ValueError) as exc:
        return {"error": f"vault unreadable: {exc.__class__.__name__}"}


def usable_count(provider: str) -> int:
    data = vault_data()
    if "error" in data:
        return 0
    pools = data.get("keys_by_provider", {})
    pool = pools.get(provider, []) if isinstance(pools, dict) else []
    if isinstance(pool, list):
        return sum(isinstance(v, str) and v not in VIRTUAL_KEY_VALUES and "PLACEHOLDER" not in v for v in pool)
    keys = data.get("keys", {})
    entry = keys.get(provider, {}) if isinstance(keys, dict) else {}
    value = entry.get("key", "") if isinstance(entry, dict) else entry
    return int(isinstance(value, str) and value not in VIRTUAL_KEY_VALUES and "PLACEHOLDER" not in value)


def config_metadata(path: Path) -> Dict[str, List[str]]:
    result = {"models": [], "providers": [], "warnings": []}
    if not path.is_file():
        return result
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        result["warnings"].append(f"unreadable: {exc}")
        return result
    result["models"] = list(dict.fromkeys(m.group(1).rstrip(",") for m in MODEL_RE.finditer(text)))
    result["providers"] = list(dict.fromkeys(m.group(1).rstrip(",") for m in PROVIDER_RE.finditer(text)))
    if SECRET_RE.search(text):
        result["warnings"].append("possible credential field in config")
    return result


_VAULT_NAMES_CACHE: Optional[List[str]] = None


def vault_env_names() -> List[str]:
    """Every env var name bin/vault-env is able to export.

    Names only. Values are never read into the report.
    """
    global _VAULT_NAMES_CACHE
    if _VAULT_NAMES_CACHE is not None:
        return _VAULT_NAMES_CACHE
    names = set()
    data = vault_data()
    if "error" not in data:
        keys = data.get("keys", {})
        keys = keys if isinstance(keys, dict) else {}
        pools = data.get("keys_by_provider", {})
        pools = pools if isinstance(pools, dict) else {}
        for provider in set(keys) | set(pools):
            entry = keys.get(provider, {})
            explicit = entry.get("env_var") if isinstance(entry, dict) else None
            name = explicit or VAULT_ENV_FALLBACK.get(provider)
            if name:
                names.add(name)
                names.add(f"{name}_POOL")
        infra = data.get("infra", {})
        if isinstance(infra, dict):
            for section in infra.values():
                if isinstance(section, dict):
                    names.update(k for k in section if LOOKS_SECRET_RE.search(k))
    names.update(VAULT_ENV_ALIASES)
    _VAULT_NAMES_CACHE = sorted(names)
    return _VAULT_NAMES_CACHE


def _iter_scan_files() -> Iterable[Path]:
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            here = Path(current)
            try:
                depth = len(here.relative_to(root).parts)
            except ValueError:
                continue
            if depth >= SCAN_MAX_DEPTH:
                dirs[:] = []
            else:
                dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
            for filename in files:
                candidate = here / filename
                if candidate.suffix.lower() in SCAN_SUFFIXES:
                    yield candidate
    # Top-level dotfiles such as .oxrc / .aider.conf.yml live outside SCAN_ROOTS.
    try:
        for candidate in Path.home().iterdir():
            if candidate.is_file() and candidate.name.startswith(".") \
                    and candidate.suffix.lower() in SCAN_SUFFIXES:
                yield candidate
    except OSError:
        return


def scan_for_requirements() -> Dict[str, List[str]]:
    """Map env var name -> files that reference it.

    Picks up `{env:NAME}` placeholders, `$NAME` / `${NAME}` expansions, and
    names that only appear in a skip-worthy "no key required" field.
    """
    requirements: Dict[str, List[str]] = {}
    inspected = 0
    for path in _iter_scan_files():
        if inspected >= SCAN_MAX_FILES:
            break
        inspected += 1
        try:
            text = path.read_text(errors="replace")[:20000]
        except OSError:
            continue
        names = set(ENV_PLACEHOLDER_RE.findall(text))
        names.update(n for n in ENV_REF_RE.findall(text) if LOOKS_SECRET_RE.search(n))
        try:
            shown = str(path.relative_to(Path.home()))
        except ValueError:
            shown = str(path)
        for name in names:
            if not LOOKS_SECRET_RE.search(name) or name.endswith("_POOL"):
                continue
            bucket = requirements.setdefault(name, [])
            if shown not in bucket:
                bucket.append(shown)
    return requirements


def missing_credentials() -> List[Dict[str, object]]:
    """Referenced credentials that are neither in the live env nor in the vault."""
    vault_names = set(vault_env_names())
    gaps: List[Dict[str, object]] = []
    for name, sources in sorted(scan_for_requirements().items()):
        if os.environ.get(name):
            continue
        in_vault = name in vault_names
        gaps.append({
            "var": name,
            "status": "in vault, not exported (needs `source bin/vault-env`)" if in_vault
                      else "ABSENT (no key in env or vault)",
            "in_vault": in_vault,
            "sources": sources[:4],
            "references": len(sources),
        })
    return gaps


# ── endpoint reachability + wire-protocol capability ─────────────────────────
# The bug class this exists to catch: a CLI configured for a protocol its
# endpoint does not serve, or pointed at a port nothing is listening on. Both
# happened on 2026-09-15 -- Codex 0.151 dropped chat/completions support
# outright, and 9router, the Responses-capable router every CLI pointed at, was
# simply not running. Neither was visible from any single config, which is why
# this is a cross-check rather than another per-CLI field.

# host:port -> wire APIs actually verified to work there.
ENDPOINT_CAPABILITIES: Dict[str, set] = {
    # bin/ai_gateway.py serves /v1[...]/chat/completions only and 404s
    # /v1/responses, so it can never back Codex >= 0.151.
    "127.0.0.1:7071": {"chat"},
    "localhost:7071": {"chat"},
    # 9router: both verified live 2026-09-15.
    "127.0.0.1:20128": {"chat", "responses"},
    "localhost:20128": {"chat", "responses"},
    "127.0.0.1:8080": {"chat"},
    "localhost:8080": {"chat"},
}

BASE_URL_RE = re.compile(r"(?:base_?url|api_base)\s*[\"']?\s*[:=]\s*[\"']([^\"']+)")
WIRE_API_RE = re.compile(r"wire_api\s*=\s*[\"']?(chat|responses)")
API_BACKEND_RE = re.compile(r"api_backend\s*=\s*[\"']([a-z_]+)")


def listening_ports() -> set:
    """Local TCP ports in LISTEN state, read out of procfs.

    Procfs rather than a socket connect: it sends no packets, so the module's
    "no network is contacted" promise still holds.
    """
    ports = set()
    for proc_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(proc_file).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "0A":  # 0A is TCP_LISTEN
                try:
                    ports.add(int(fields[1].rsplit(":", 1)[1], 16))
                except (IndexError, ValueError):
                    continue
    return ports


def _hostport(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url.strip())
    return (match.group(1) if match else url.strip()).rstrip("/")


def endpoint_findings() -> List[str]:
    """Configs whose endpoint is down, or cannot speak the declared protocol."""
    listening = listening_ports()
    findings: List[str] = []
    for relative in (".codex/config.toml", ".grok/config.toml",
                     ".config/opencode/opencode.json", ".hermes/config.yaml",
                     ".aider.conf.yml"):
        path = Path.home() / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        urls = list(dict.fromkeys(BASE_URL_RE.findall(text)))
        wire = WIRE_API_RE.search(text)
        backend = API_BACKEND_RE.search(text)
        wants = (wire.group(1) if wire
                 else "chat" if backend and backend.group(1).startswith("chat")
                 else None)
        for url in urls:
            hostport = _hostport(url)
            if hostport.startswith(("127.0.0.1:", "localhost:", "0.0.0.0:")):
                try:
                    port = int(hostport.rsplit(":", 1)[1])
                except ValueError:
                    continue
                if port not in listening:
                    findings.append(
                        f"{relative}: nothing is listening on {hostport} "
                        f"-> every call fails")
            capabilities = ENDPOINT_CAPABILITIES.get(hostport)
            if wants and capabilities and wants not in capabilities:
                findings.append(
                    f"{relative}: declares {wants} but {hostport} serves only "
                    f"{'/'.join(sorted(capabilities))} -> cannot work")
    return findings


def _py_dict_keys(path: Path, varname: str) -> List[str]:
    """Keys of a module-level ``NAME = { ... }`` literal, without importing.

    Not an import on purpose: importing bin/vault_ingest.py runs a rglob across
    the tree at module scope, which a bounded, read-only doctor run cannot pay
    for.
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    head = re.search(rf"^{re.escape(varname)}\s*=\s*\{{", text, re.M)
    if not head:
        return []
    start = head.end() - 1
    depth = 0
    end = len(text)
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    keys = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)"\s*:', text[start:end], re.M)
    return list(dict.fromkeys(keys))


def _prefix_map_providers(path: Path) -> List[str]:
    """Providers named in vault_ingest.PREFIX_MAP (second tuple column)."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    head = re.search(r"^PREFIX_MAP\s*=\s*\[", text, re.M)
    if not head:
        return []
    start = head.end()
    stop = text.find("\n]", start)
    body = text[start:stop if stop != -1 else len(text)]
    return list(dict.fromkeys(
        re.findall(r'\(\s*r?"[^"]*"\s*,\s*"([A-Za-z0-9_.\-]+)"', body)))


def registry_divergence() -> Dict[str, List[str]]:
    """Where the hand-maintained provider lists disagree with the vault.

    Four separate artifacts each describe which providers exist, so they drift.
    A pool with no route is a key that arrived and can never be used; a route
    with no pool is a provider that can never answer.
    """
    pools: set = set()
    data = vault_data()
    if "error" not in data:
        keys_by_provider = data.get("keys_by_provider", {})
        if isinstance(keys_by_provider, dict):
            pools = {provider for provider, keys in keys_by_provider.items() if keys}
    home = Path.home()
    routes = set(_py_dict_keys(home / "bin/ai_gateway.py", "PROVIDERS"))
    exported = set(_py_dict_keys(home / "bin/vault-env", "mapping"))
    ingestible = set(_prefix_map_providers(home / "bin/vault_ingest.py"))
    return {
        "pool_no_route": sorted(pools - routes),
        "route_no_pool": sorted(routes - pools),
        "pool_no_env": sorted(pools - exported),
        "ingest_no_pool": sorted(ingestible - pools),
    }


# Divergences that are DECISIONS, not defects, each paired with the reason.
#
# --quiet subtracts these. Without this list a healthy machine reports problems
# forever, and a permanent alarm is not a signal -- the daily report becomes
# noise and stops being read, which is the same failure as no report at all.
#
# Rule for adding an entry: the reason must say WHY, not that it is fine. An
# entry with no reason is a bug, because nobody reading this later can tell an
# intentional exclusion from a forgotten one -- which is precisely the gap this
# whole section exists to close. Keep in step with HANDOFF_20260915_CLI_STACK.md
# section 3.
ACCEPTED_DIVERGENCE: Dict[str, Dict[str, str]] = {
    "pool_no_route": {
        "vibe": "all 6 keys are byte-identical to mistral's; wiring it would "
                "round-robin the same keys twice and corrupt per-key cooldown",
        "jules": "8 keys; jules is a separate Google product, so its tokens may "
                 "be intended for jules rather than for gemini",
    },
    "route_no_pool": {
        "deepinfra": "endpoint verified, no key has been purchased",
        "sambanova": "host was unreachable at last probe; do not buy here until fixed",
    },
    "pool_no_env": {},
    "ingest_no_pool": {},
}


def unaccepted_divergence() -> Dict[str, List[str]]:
    """Registry divergence minus the entries that are deliberate.

    This -- not the raw divergence -- is what a scheduled check should report.
    """
    accepted = ACCEPTED_DIVERGENCE
    result: Dict[str, List[str]] = {}
    for key, values in registry_divergence().items():
        leftover = [value for value in values if value not in accepted.get(key, {})]
        if leftover:
            result[key] = leftover
    return result


def row(name: str) -> Dict[str, object]:
    spec = CLIS[name]
    entries = path_entries(name)
    defaults = spec.get("defaults", ())
    models = list(defaults[:1])
    providers = list(defaults[1:2])
    configs = []
    warnings = []
    for relative in spec["configs"]:
        path = Path.home() / relative
        meta = config_metadata(path)
        if path.is_file():
            configs.append(str(path))
        models.extend(m for m in meta["models"] if m not in models)
        providers.extend(p for p in meta["providers"] if p not in providers)
        warnings.extend(f"{relative}: {warning}" for warning in meta["warnings"])
    canonical = CANONICAL_PATHS[name].resolve()
    return {
        "name": name,
        "entries": entries,
        "canonical": str(canonical),
        "canonical_first": bool(entries and Path(entries[0]).resolve() == canonical),
        "target": target_for(Path(entries[0])) if entries else "not found",
        "auth": ("available (" + ", ".join(f"vault:{p}={usable_count(p)}" for p in spec.get("vault", ()) if usable_count(p)) + "; not live-tested)"),
        "configs": configs,
        "models": models,
        "providers": providers,
        "warnings": warnings,
        "env_unexported": [v for v in spec["env"]
                           if not os.environ.get(v) and v in set(vault_env_names())],
        "env_absent": [v for v in spec["env"]
                       if not os.environ.get(v) and v not in set(vault_env_names())],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing and exit 0 when nothing is wrong. Makes this "
                             "safe on a timer: silence means healthy, output means look. "
                             "Exits 1 when it does print, so systemd records the failure.")
    args = parser.parse_args(argv)

    # Quiet mode answers one question -- "is anything wrong?" -- and is checked
    # before the (relatively expensive) full report, so a healthy daily run costs
    # almost nothing. The three problem sources are exactly the three sections
    # the full report adds beyond the per-CLI table.
    if args.quiet and not args.json:
        problems = list(endpoint_findings())
        problems += [gap["var"] for gap in missing_credentials()]
        # unaccepted_divergence, not registry_divergence: the accepted entries are
        # decisions, and re-reporting them daily would make silence impossible.
        for values in unaccepted_divergence().values():
            problems += values
        if not problems:
            return 0

    rows = [row(name) for name in CLIS]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print("CLI DOCTOR (read-only; no inference, network, vault load, or writes)")
    print("=" * 78)
    for item in rows:
        print(f"\n{item['name']}")
        print(f"  PATH:   {' | '.join(item['entries']) if item['entries'] else 'NOT FOUND'}")
        print(f"  CANON:  {item['canonical']} ({'first' if item['canonical_first'] else 'not first'})")
        print(f"  TARGET: {item['target']}")
        print(f"  AUTH:   {item['auth'] or 'missing'}")
        print(f"  CONFIG: {', '.join(item['configs']) if item['configs'] else 'none inspected'}")
        print(f"  MODEL:  {', '.join(item['models']) if item['models'] else 'not declared'}")
        print(f"  PROV:   {', '.join(item['providers']) if item['providers'] else 'not declared'}")
        for warning in item["warnings"]:
            print(f"  WARN:   {warning}")
        if item["env_unexported"]:
            print(f"  KEYS:   declared but not exported: {', '.join(item['env_unexported'])}")
        if item["env_absent"]:
            print(f"  KEYS:   NO KEY ANYWHERE: {', '.join(item['env_absent'])}")
    collisions = {item["name"]: item["entries"] for item in rows if len(item["entries"]) > 1}
    print("\nOVERLAP")
    print("  Distinct binaries in PATH: " + ("; ".join(f"{k} ({len(v)})" for k, v in collisions.items()) if collisions else "none detected"))

    print("\nCREDENTIALS (referenced by config; names only, values never read)")
    gaps = missing_credentials()
    if not gaps:
        print("  every referenced credential resolves from the live env or the vault")
    else:
        for gap in gaps:
            print(f"  {gap['var']}")
            print(f"      {gap['status']}")
            for source in gap["sources"]:
                print(f"      <- {source}")

    # In --quiet mode we only get here when something is wrong, so announce that
    # up front; a bare report with no framing reads like noise on a timer.
    if args.quiet:
        print("CLI DOCTOR: something needs attention")

    print("\nENDPOINTS (reachability + wire protocol; procfs only, no packets sent)")
    findings = endpoint_findings()
    if not findings:
        print("  every declared endpoint is listening and speaks the declared protocol")
    else:
        for finding in findings:
            print(f"  FAIL  {finding}")

    print("\nPROVIDER REGISTRY (vault pools vs gateway routes vs env export vs ingest rules)")
    divergence = registry_divergence()
    labels = {
        "pool_no_route": "in the vault, but bin/ai_gateway.py has no route (key can never be used)",
        "route_no_pool": "routed by the gateway, but no key in the vault (route is inert)",
        "pool_no_env": "pooled in the vault, but bin/vault-env exports no variable for it",
        "ingest_no_pool": "has an ingest rule, but no key has ever landed",
    }
    if not any(divergence.values()):
        print("  all four lists agree")
    else:
        for key, label in labels.items():
            values = divergence.get(key) or []
            fresh = [v for v in values if v not in ACCEPTED_DIVERGENCE.get(key, {})]
            accepted = [v for v in values if v in ACCEPTED_DIVERGENCE.get(key, {})]
            if fresh:
                print(f"  {label}")
                print(f"      {', '.join(fresh)}")
            if accepted:
                print(f"  accepted (a decision, not a defect): {', '.join(accepted)}")
                for value in accepted:
                    print(f"      {value}: {ACCEPTED_DIVERGENCE[key][value]}")
    # Exit 1 only in quiet mode, so a timer can tell healthy from needs-attention
    # without changing the exit code of the interactive report.
    if args.quiet:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
