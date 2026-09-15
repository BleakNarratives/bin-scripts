#!/usr/bin/env python3
# [DNA_TAG]
# ORIGIN: Crostini-Chromebook (auto-detected)
# PILLAR: unknown
# DEPS: memory
# ROLE: Component of UNPACKED
# AUTHOR: Auto-tagged by Buffy (DNA Sweeper)
# SESSION: 2026-08-22 ShipWreckD OS Builder
# TIER: Scout (6)
# AKA: anchor, intake_2026-08-22-rclone-ingest_anchor_collective-module
# [/DNA_TAG]

import sys, os, json, subprocess, time
sys.path.insert(0, os.path.expanduser("~/bin/anchor"))
import memory, tools, models as mdl, patcher

def call_model(prompt, model=None):
    """Route the request through the shared client (bin/ai_lib.py -> the
    rotating gateway).

    Puter is retired: ~/.puter_token does not exist, so the old node bridge
    could only ever print "[puter_bridge] No token found" and exit 1. Every
    Anchor request was therefore dead at the transport layer, not just slow.
    """
    sys.path.insert(0, os.path.expanduser("~/bin"))
    import ai_lib
    return ai_lib.ask(prompt, model=model or None)

def build_context():
    # Project/user context comes from the persistent store, so `anchor
    # --remember project '{"name": "x"}'` survives across runs.
    def _load_fact(key):
        raw = memory.recall(key)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {"value": raw}

    project, user = _load_fact("project"), _load_fact("user")

    history = memory.get_recent(6)
    ctx  = f"## Who you're helping\n{json.dumps(user, indent=2)}\n\n"
    ctx += f"## Project state\n{json.dumps(project, indent=2)}\n\n"
    if history:
        ctx += "## Recent history\n"
        for msg in history:
            ctx += f"[{msg.get('role', '?')}]: {str(msg.get('content', ''))[:500]}\n"
    return ctx

def read_stdin():
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return None

def main():
    args = sys.argv[1:]

    # ── persistent state and search (no model call) ──────────────────────────
    if args and args[0] == "--remember":
        memory.remember(args[1], " ".join(args[2:]))
        print(f"[anchor] remembered {args[1]}")
        return
    if args and args[0] == "--recall":
        value = memory.recall(args[1])
        print(value if value is not None else f"[anchor] nothing stored for {args[1]!r}")
        return
    if args and args[0] == "--facts":
        import store
        for fact in store.all_facts():
            print(f"{fact['key']} = {fact['value']}")
        return
    if args and args[0] == "--stats":
        print(memory.stats())
        return
    if args and args[0] == "--web":
        import web_search
        print(web_search.format_results(web_search.search(" ".join(args[1:]))))
        return

    # handle flags that don't need a model
    if not args and sys.stdin.isatty():
        print("Usage: anchor <prompt>")
        print("       anchor --feed file1.py file2.py 'prompt'")
        print("       cat file.py | anchor 'what is wrong with this'")
        print("       anchor --run 'bash command'")
        print("       anchor --read path/to/file")
        print("       anchor --search ~/bin pattern")
        print("       anchor --patch file 'old' 'new'")
        print("       anchor --refresh-models")
        sys.exit(0)

    # `args and` is load-bearing: the TTY usage guard above does not fire when
    # stdin is not a terminal, so cron, scripts, pipes and other agents all
    # reached these bare args[0] reads and died with IndexError. Matches the
    # convention the --stats/--web blocks above already use.
    if args and args[0] == "--refresh-models":
        ok = mdl.refresh_models()
        print("[OK] models refreshed" if ok else "[FAIL]")
        return

    if args and args[0] == "--run":
        print(tools.run_shell(" ".join(args[1:])))
        return

    if args and args[0] == "--read":
        print(tools.read_file(args[1]))
        return

    if args and args[0] == "--search":
        print(patcher.search_codebase(args[1], args[2]))
        return

    if args and args[0] == "--patch":
        print(patcher.apply_patch(args[1], args[2], args[3]))
        return

    # collect fed files
    fed_content = ""
    if args and args[0] == "--feed":
        args = args[1:]
        while args and os.path.exists(os.path.expanduser(args[0])):
            path = args.pop(0)
            content = tools.read_file(path)
            fed_content += f"\n## File: {path}\n```\n{content[:3000]}\n```\n"

    # collect prompt
    prompt = " ".join(args) if args else ""

    # pipe support: cat file.py | anchor "what's wrong"
    piped = read_stdin()
    if piped:
        fed_content += f"\n## Piped input\n```\n{piped[:4000]}\n```\n"

    if not prompt and not piped:
        print("[anchor] no prompt")
        sys.exit(1)

    # auto-include live scan if no files fed and prompt mentions bugs/swarm/scan
    if not fed_content and any(w in prompt.lower() for w in ["bug","swarm","scan","fix","broken","metamorph","skill"]):
        try:
            hits = tools.live_bug_scan()
            fed_content += "\n## Live scan results\n" + "\n".join(hits) + "\n"
        except:
            pass

    model, task = mdl.pick_model(prompt or piped)
    print(f"[anchor] task={task} model={model}")

    context   = build_context()
    full      = context + fed_content + f"\n## Request\n{prompt}"

    memory.push("user", (prompt or "[piped input]")[:300])
    t0       = time.time()
    response = call_model(full, model)
    latency  = int((time.time()-t0)*1000)

    success = bool(response) and not response.startswith("[puter_bridge]")
    mdl.record_perf(model, task, success, latency)
    memory.push("anchor", response[:500])
    print(response)

if __name__ == "__main__":
    main()
