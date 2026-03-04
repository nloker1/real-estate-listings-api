#!/bin/bash

# 1. DEFINE PATHS
# Use the absolute path to your project
PROJECT_DIR="/root/real-estate-listings-api"

# Use the python executable INSIDE your venv
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

# Define the log file
LOG_FILE="$PROJECT_DIR/sync.log"

# 2. START LOGGING
echo "--------------------------------------------------" >> "$LOG_FILE"
echo "Starting Sync: $(date)" >> "$LOG_FILE"

# 3. RUN THE RMLS SYNC
# We navigate to the folder first to ensure imports work
cd "$PROJECT_DIR"

echo "Running RMLS Data Sync..." >> "$LOG_FILE"
# We run python using the VENV path
"$VENV_PYTHON" -m app.services.rmls_api >> "$LOG_FILE" 2>&1

# 4. RUN THE ALERT WORKER
# This checks for matches against new properties pulled in the step above
echo "Checking for Saved Search Matches..." >> "$LOG_FILE"
export PYTHONPATH=$PROJECT_DIR
"$VENV_PYTHON" app/services/alert_worker.py >> "$LOG_FILE" 2>&1

# 5. FINISH
echo "Finished Everything: $(date)" >> "$LOG_FILE"
