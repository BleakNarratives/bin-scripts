#!/bin/bash
# INSTANT REPORT - No grepping big files, no hanging scripts
REPORT=~/instant_report.json
echo "{" > $REPORT
echo "\"timestamp\":\"$(date -Iseconds)\"," >> $REPORT

# Check key dirs (just stat them)
for d in ~/MikeySwarm ~/payment_system ~/cloud_bridge ~/Official-Vertical-AI-Boardroom; do
    if [ -d "$d" ]; then
        size=$(du -sh "$d" 2>/dev/null | cut -f1)
        latest=$(ls -lt "$d" | head -2 | tail -1 | awk '{print $6,$7,$8}')
        echo "\"$d\":{\"exists\":true,\"size\":\"$size\",\"latest\":\"$latest\"}," >> $REPORT
    else
        echo "\"$d\":{\"exists\":false}," >> $REPORT
    fi
done

# Check the monolith exists (just check, don't read)
if [ -f ~/NAT-Recovery-Kernel/intake/2026-08-22-rclone-juris-swarm/MASTER_CODE_LIBRARY.py ]; then
    echo "\"monolith\":\"present\"," >> $REPORT
else
    echo "\"monolith\":\"missing\"," >> $REPORT
fi

# Run overseer with a 2-second timeout
if [ -f ~/cloud_bridge/overseer.py ]; then
    timeout 2 python3 ~/cloud_bridge/overseer.py --status 2>/dev/null || echo "\"overseer_status\":\"timeout_or_error\"," >> $REPORT
else
    echo "\"overseer\":\"not_found\"," >> $REPORT
fi

# Close JSON
echo "\"done\":true}" >> $REPORT

cat $REPORT
