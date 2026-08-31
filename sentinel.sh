#!/bin/bash
# Sentinel.sh - The Ward
# Intercepts, stages, and validates code execution.

STAGED_FILE="/tmp/ward_staging"
LOG_FILE="$HOME/.ward/traceback.log"

# 1. Capture incoming code from stdin
cat > "$STAGED_FILE"

# Ensure log directory exists
mkdir -p "$HOME/.ward"

# 2. Invoke the Sniffer/Validator (placeholder for actual tool path)
# Replace 'sniffer_logic' with the actual validator command
sniffer_logic "$STAGED_FILE"
VALIDATION_STATUS=$?

# 3. Check status
if [ $VALIDATION_STATUS -eq 0 ]; then
    # Approved: Execute and cleanup
    cat "$STAGED_FILE" | bash
    rm "$STAGED_FILE"
    exit 0
else
    # Blocked: Log and exit
    echo "[$(date)] Validation failed for $STAGED_FILE" >> "$LOG_FILE"
    echo "WARD: Code blocked due to validation failure."
    exit 1
fi
