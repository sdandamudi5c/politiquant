#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# PolitiQuant — Cron Setup
# Schedules daily_job.py to run automatically every night at midnight.
#
# What the daily job does:
#   1. Scores congressional-purchase universe (stocks politicians bought)
#   2. Resolves 30-day outcomes for previously scored stocks
#   3. Retrains the XGBoost ML model with new outcomes
#   4. Keeps score_history.json growing so predictions improve over time
#
# Usage:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh
#
# To remove the cron job later:
#   crontab -e   (delete the PolitiQuant line)
# ──────────────────────────────────────────────────────────────────────────────

set -e

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python3"
JOB="$SCRIPT_DIR/daily_job.py"
LOG="$SCRIPT_DIR/daily_job.log"

# Verify venv exists
if [ ! -f "$PYTHON" ]; then
    echo "❌  Virtual environment not found at $SCRIPT_DIR/venv"
    echo "    Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Verify daily_job.py exists
if [ ! -f "$JOB" ]; then
    echo "❌  daily_job.py not found at $JOB"
    exit 1
fi

# The cron line: run at midnight every day, log output
CRON_LINE="0 0 * * * $PYTHON $JOB >> $LOG 2>&1"
MARKER="# PolitiQuant daily job"

# Check if already installed
if crontab -l 2>/dev/null | grep -q "PolitiQuant daily job"; then
    echo "✅  Cron job already installed. Current schedule:"
    crontab -l 2>/dev/null | grep "PolitiQuant" -A1
    echo ""
    read -p "Re-install it? (y/N): " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "Cancelled."
        exit 0
    fi
    # Remove old entry
    crontab -l 2>/dev/null | grep -v "PolitiQuant" | grep -v "daily_job" | crontab -
fi

# Add new cron entry
(crontab -l 2>/dev/null; echo "$MARKER"; echo "$CRON_LINE") | crontab -

echo ""
echo "✅  Cron job installed!"
echo ""
echo "   Schedule : Every day at midnight (00:00)"
echo "   Command  : $PYTHON $JOB"
echo "   Log file : $LOG"
echo ""
echo "   To check it's installed:"
echo "   crontab -l"
echo ""
echo "   To watch the log:"
echo "   tail -f $LOG"
echo ""
echo "   To run manually right now:"
echo "   $PYTHON $JOB"
echo ""

# Offer to run once immediately to seed the model
read -p "Run daily_job.py now to seed the ML model? (y/N): " RUN_NOW
if [[ "$RUN_NOW" == "y" || "$RUN_NOW" == "Y" ]]; then
    echo ""
    echo "Running daily_job.py..."
    "$PYTHON" "$JOB"
    echo ""
    echo "✅  Done! Check $LOG for details on future runs."
fi
