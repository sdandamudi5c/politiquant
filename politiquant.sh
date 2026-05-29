#!/bin/bash
# PolitiQuant control script
# Usage:
#   ./politiquant.sh start    — start (or restart)
#   ./politiquant.sh stop     — stop
#   ./politiquant.sh status   — is it running?
#   ./politiquant.sh logs     — tail the live log
#   ./politiquant.sh install  — register auto-start on login
#   ./politiquant.sh uninstall — remove auto-start

PLIST_LABEL="com.politiquant.app"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LOG_PATH="$HOME/Library/Logs/politiquant.log"
ERR_PATH="$HOME/Library/Logs/politiquant.error.log"

case "$1" in

  start)
    if launchctl list | grep -q "$PLIST_LABEL" 2>/dev/null; then
      echo "⟳  Restarting PolitiQuant…"
      launchctl stop "$PLIST_LABEL"
      sleep 1
      launchctl start "$PLIST_LABEL"
    else
      echo "▶  Starting PolitiQuant…"
      launchctl load "$PLIST_PATH" 2>/dev/null || {
        # Not installed as launch agent — run directly
        cd "$(dirname "$0")"
        source venv/bin/activate
        nohup streamlit run app.py \
          --server.port 8501 \
          --server.headless true \
          --browser.gatherUsageStats false \
          > "$LOG_PATH" 2>"$ERR_PATH" &
        echo "▶  PolitiQuant started (PID $!)"
        echo "   Open: http://localhost:8501"
        echo "   Logs: $LOG_PATH"
      }
    fi
    ;;

  stop)
    if launchctl list | grep -q "$PLIST_LABEL" 2>/dev/null; then
      launchctl stop "$PLIST_LABEL"
      echo "⏹  PolitiQuant stopped (will restart on next login — run 'uninstall' to prevent)"
    else
      pkill -f "streamlit run app.py" && echo "⏹  PolitiQuant stopped" || echo "Not running"
    fi
    ;;

  status)
    if pgrep -f "streamlit run app.py" > /dev/null; then
      PID=$(pgrep -f "streamlit run app.py")
      echo "✅  PolitiQuant is running (PID $PID)"
      echo "   Open: http://localhost:8501"
    else
      echo "⏹  PolitiQuant is not running"
    fi
    ;;

  logs)
    echo "📋  Tailing $LOG_PATH  (Ctrl+C to stop)"
    tail -f "$LOG_PATH"
    ;;

  install)
    echo "⚙️  Installing auto-start launch agent…"
    mkdir -p "$HOME/Library/LaunchAgents"
    mkdir -p "$HOME/Library/Logs"

    APP_DIR="$(cd "$(dirname "$0")" && pwd)"
    STREAMLIT="$APP_DIR/venv/bin/streamlit"

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${STREAMLIT}</string>
        <string>run</string>
        <string>${APP_DIR}/app.py</string>
        <string>--server.port</string>
        <string>8501</string>
        <string>--server.headless</string>
        <string>true</string>
        <string>--browser.gatherUsageStats</string>
        <string>false</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${APP_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${LOG_PATH}</string>

    <key>StandardErrorPath</key>
    <string>${ERR_PATH}</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

    launchctl load "$PLIST_PATH"
    echo "✅  PolitiQuant will now auto-start on every login"
    echo "   Open: http://localhost:8501"
    echo "   Logs: $LOG_PATH"
    echo ""
    echo "   To stop auto-start: ./politiquant.sh uninstall"
    ;;

  uninstall)
    launchctl unload "$PLIST_PATH" 2>/dev/null
    rm -f "$PLIST_PATH"
    echo "🗑  Auto-start removed. PolitiQuant will no longer start on login."
    ;;

  *)
    echo "PolitiQuant control script"
    echo ""
    echo "Usage: ./politiquant.sh <command>"
    echo ""
    echo "Commands:"
    echo "  install    Register auto-start on every Mac login"
    echo "  uninstall  Remove auto-start"
    echo "  start      Start (or restart) the app now"
    echo "  stop       Stop the app"
    echo "  status     Check if running"
    echo "  logs       Tail the live log"
    ;;
esac
