#!/usr/bin/env bash
# hc — humane cannibal shorthand
# hc          → dry-run boardroom
# hc --execute → actually archive+graft
python3 ~/tools/introspection/humane_cannibal.py "${2:-~/Official-Vertical-AI-Boardroom}" "${1:---dry-run}"
