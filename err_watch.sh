#!/usr/bin/env bash
LAST_CLIP=""
while true; do
    CLIP=$(xclip -selection clipboard -o 2>/dev/null)
    if [[ "$CLIP" != "$LAST_CLIP" && -n "$CLIP" ]]; then
        LAST_CLIP="$CLIP"
        if echo "$CLIP" | grep -Eq "Traceback|SyntaxError|ImportError|Unterminated string|Error:|FAILED|Exception"; then
            echo -e "\n\033[1;33m[!] Error captured from clipboard! Logging...\033[0m"
            echo "$CLIP" > /tmp/last_terminal_error.log
        fi
    fi
    sleep 1
done
