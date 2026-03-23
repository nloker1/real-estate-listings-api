#!/bin/bash

# 1. DEFINE PATHS
PROJECT_DIR="/root/real-estate-listings-api"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

# Define separate log files
SYNC_LOG="$PROJECT_DIR/sync.log"
ALERT_LOG="$PROJECT_DIR/alerts.log"

# 2. START LOGGING
echo "--------------------------------------------------" >> "$SYNC_LOG"
echo "Starting Sync: $(date)" >> "$SYNC_LOG"
echo "--------------------------------------------------" >> "$ALERT_LOG"
echo "Starting Alert Worker: $(date)" >> "$ALERT_LOG"

# 3. RUN THE RMLS SYNC
cd "$PROJECT_DIR"
echo "Running RMLS Data Sync..." >> "$SYNC_LOG"
"$VENV_PYTHON" -m app.services.rmls_api >> "$SYNC_LOG" 2>&1

# 4. RUN THE ALERT WORKER
echo "Running Alert Worker..." >> "$ALERT_LOG"
# We use PYTHONPATH to ensure the 'app' module is discoverable
PYTHONPATH="$PROJECT_DIR" "$VENV_PYTHON" app/services/alert_worker.py >> "$ALERT_LOG" 2>&1

# 5. FINISH
echo "Finished Sync: $(date)" >> "$SYNC_LOG"
echo "Finished Alert Worker: $(date)" >> "$ALERT_LOG"
