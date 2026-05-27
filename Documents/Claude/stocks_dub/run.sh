#!/bin/bash
# PolitiQuant — manual start script
# Usage: ./run.sh
cd "$(dirname "$0")"
source venv/bin/activate
streamlit run app.py \
  --server.port 8501 \
  --server.headless false \
  --browser.gatherUsageStats false
