#!/bin/bash

# Define where you want the logs to live
LOG_FILE="/root/real-estate-listings-api/sync.log"

# Navigate to the project directory
cd /root/real-estate-listings-api

# Print a divider line with the current date into the log file
echo "--------------------------------------------------" >> "$LOG_FILE"
echo "Starting Sync: $(date)" >> "$LOG_FILE"

# Run the python script
# '>>' appends standard output to the file
# '2>&1' captures errors and puts them in the same file
python3 -m app.services.rmls_api >> "$LOG_FILE" 2>&1

# Print end time
echo "Finished Sync: $(date)" >> "$LOG_FILE"#!/bin/bash
# Navigate to the project directory
cd ~/real-estate-listings-api

# Activate your virtual environment
source venv/bin/activate

# Run the script
python3 -m app.services.rmls_api
