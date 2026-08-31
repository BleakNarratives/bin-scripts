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

def call_puter(prompt, model=None):
    model = model or mdl.DEFAULT_MODEL
    r = subprocess.run(
        ["node", os.path.expanduser("~/bin/puter_bridge.js"), prompt],
        capture_output=True, text=True
    )
    out = r.stdout.strip()
    # extract just the text content from the response
    try:
        data = json.loads(out)
        return data.get("message",{}).get("content", out)
    except:
        return out

def build_context():
    try:
        from memory import get_state
        project = get_state("project") or {}
        user    = get_state("user") or {}
    except:
        project, user = {}, {}

    history = memory.get_recent(6)
    ctx  = f"## Who you're helping\n{json.dumps(user, indent=2)}\n\n"
    ctx += f"## Project state\n{json.dumps(project, indent=2)}\n\n"
    if history:
        ctx += "## Recent history\n"
        for role, content in history:
            ctx += f"[{role}]: {content[:500]}\n"
    return ctx

def read_stdin():
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return None

def main():
    args = sys.argv[1:]

    # handle flags that don't need puter
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

    if args[0] == "--refresh-models":
        ok = mdl.refresh_models()
        print("[OK] models refreshed" if ok else "[FAIL]")
        return

    if args[0] == "--run":
        print(tools.run_shell(" ".join(args[1:])))
        return

    if args[0] == "--read":
        print(tools.read_file(args[1]))
        return

    if args[0] == "--search":
        print(patcher.search_codebase(args[1], args[2]))
        return

    if args[0] == "--patch":
        print(patcher.apply_patch(args[1], args[2], args[3]))
        return

    # collect fed files
    fed_content = ""
    if args[0] == "--feed":
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
    response = call_puter(full, model)
    latency  = int((time.time()-t0)*1000)

    success = bool(response) and not response.startswith("[puter_bridge]")
    mdl.record_perf(model, task, success, latency)
    memory.push("anchor", response[:500])
    print(response)

if __name__ == "__main__":
    main()
