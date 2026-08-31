#!/usr/bin/env bash
WATCH_DIR="$HOME/Downloads"
STAGING_DIR="$HOME/._incoming/staging"
PROD_DIR="$HOME/Official-Vertical-AI-Boardroom"

mkdir -p "$STAGING_DIR" "$WATCH_DIR"

echo "[MotherBrain] Monitoring $WATCH_DIR for incoming archives..."

inotifywait -m -e close_write --format '%w%f' "$WATCH_DIR" | while read FILE; do
    if [[ "$FILE" == *.zip || "$FILE" == *.tar.gz ]]; then
        echo -e "\n\033[1;32m[!] Archive dropped: $FILE\033[0m"
        rm -rf "${STAGING_DIR:?}"/*
        if [[ "$FILE" == *.zip ]]; then
            unzip -q "$FILE" -d "$STAGING_DIR"
        else
            tar -xzf "$FILE" -C "$STAGING_DIR"
        fi
        python3 ~/bin/orchestrate_ingestion.py "$STAGING_DIR" "$PROD_DIR"
        echo "[MotherBrain] Sweep complete."
    fi
done
