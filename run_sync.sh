#!/bin/bash
# Navigate to the project directory
cd ~/real-estate-listings-api

# Activate your virtual environment
source venv/bin/activate

# Run the script
python3 -m app.services.rmls_api
