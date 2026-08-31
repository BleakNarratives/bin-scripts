# [DNA_TAG]
# ORIGIN: Crostini-Chromebook (auto-detected)
# PILLAR: unknown
# DEPS: tomllib
# ROLE: Component of UNPACKED
# AUTHOR: Auto-tagged by Buffy (DNA Sweeper)
# SESSION: 2026-08-22 ShipWreckD OS Builder
# TIER: Recruit (5)
# AKA: config, anchor-module
# [/DNA_TAG]

import os
import tomllib

class Config:
    def __init__(self):
        self.path = os.path.expanduser("~/.openclaw_config.toml")
        self.data = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "rb") as f:
            return tomllib.load(f)

    def get(self, key, default=None):
        return self.data.get(key, default)
