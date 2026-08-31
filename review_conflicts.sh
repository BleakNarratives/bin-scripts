#!/usr/bin/env bash
LOG_FILE="/tmp/ingestion_conflicts.log"

if [[ ! -f "$LOG_FILE" || ! -s "$LOG_FILE" ]]; then
    echo "No conflicts logged at $LOG_FILE"
    exit 0
fi

echo -e "\n\033[1;36m=== Ingestion Conflict Review ===\033[0m"
while IFS= read -r line; do
    STAGE_PATH=$(echo "$line" | sed -n 's/.*STAGING: \(.*\) | PROD:.*/\1/p')
    PROD_PATH=$(echo "$line" | sed -n 's/.*PROD: \(.*\)/\1/p')

    echo -e "\n--------------------------------------------------"
    echo -e "\033[1;33mConflict:\033[0m $PROD_PATH"
    echo -e "Showing diff (Staging vs Prod)..."
    diff -u --color=always "$PROD_PATH" "$STAGE_PATH" | head -n 30

    read -p "Overwrite Prod with Staging version? (y/n/s to skip): " choice
    case "$choice" in
        y|Y)
            cp "$STAGE_PATH" "$PROD_PATH"
            echo "[OK] Updated $PROD_PATH"
            ;;
        *)
            echo "[SKIP] Kept original $PROD_PATH"
            ;;
    esac
done < "$LOG_FILE"

rm -f "$LOG_FILE"
echo "Conflict log cleared."
