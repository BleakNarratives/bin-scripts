#!/usr/bin/env python3
import json, re, os

keys_file = os.path.expanduser("~/dickshits.txt")
vault_file = os.path.expanduser(".concierge/vault.json")

if not os.path.exists(keys_file):
    print("Could not find ~/dickshits.txt")
    exit(1)

with open(keys_file, "r") as f:
    text = f.read()

mappings = {
    "COHERE_API_KEY": r"(?:cohere.*?)?\b(g-[\w\-]+|[\w\-]{30,})\b",
    "DEEPSEEK_API_KEY": r"(?:deepseek.*?)?\b(sk-[\w\-]{32,})\b",
    "ELEVENLABS_API_KEY": r"(?:elevenlabs.*?)?\b([\w\-]{32,})\b",
    "STABILITY_API_KEY": r"(?:stability.*?)?\b(sk-[\w\-]{32,})\b",
    "SAMBANOVA_API_KEY": r"(?:sambanova.*?)?\b([\w\-]{36})\b",
    "AIMLAPI_KEY": r"(?:aiml.*?)?\b([\w\-]{32,})\b"
}

with open(vault_file, "r") as f:
    vault = json.load(f)

updated = 0
for env_var, pattern in mappings.items():
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        key_val = match.group(1) if match.groups() else match.group(0)
        vault["keys"][env_var] = key_val
        print(f"[+] Loaded {env_var}")
        updated += 1

if updated > 0:
    with open(vault_file, "w") as f:
        json.dump(vault, f, indent=2)
    print(f"\nSuccessfully merged {updated} keys into .concierge/vault.json")
else:
    print("No matching patterns found. You can append them manually to .concierge/vault.json")
