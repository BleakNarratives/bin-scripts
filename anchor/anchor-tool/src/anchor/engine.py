# [DNA_TAG]
# ORIGIN: Crostini-Chromebook (auto-detected)
# PILLAR: unknown
# DEPS: puter
# ROLE: Component of UNPACKED
# AUTHOR: Auto-tagged by Buffy (DNA Sweeper)
# SESSION: 2026-08-22 ShipWreckD OS Builder
# TIER: Recruit (5)
# AKA: engine, anchor-module
# [/DNA_TAG]

import os
import puter

class PuterEngine:
    def __init__(self):
        token_path = os.path.expanduser("~/.puter_token")
        if not os.path.exists(token_path):
            raise Exception("No token found at ~/.puter_token")
        with open(token_path, 'r') as f:
            token = f.read().strip()
        puter.setAuthToken(token)

    def chat(self, prompt):
        try:
            response = puter.ai.chat(prompt)
            return response.message.content
        except Exception as e:
            return f"Error: {str(e)}"

    def list_models(self):
        try:
            return puter.ai.listModels()
        except Exception as e:
            return f"Error: {str(e)}"
