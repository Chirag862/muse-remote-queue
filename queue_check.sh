#!/bin/bash
# Check the queued jobs on the Render-hosted muse-remote server.
# Usage: ./queue_check.sh <render_base_url> <token>
# Example: ./queue_check.sh https://muse-remote.onrender.com <token>
# Same call with curl:
#   curl -s "https://muse-remote.onrender.com/queue?token=<token>"
set -e
BASE="${1:?usage: queue_check.sh <render_base_url> <token>}"
TOKEN="${2:?usage: queue_check.sh <render_base_url> <token>}"
curl -s "${BASE%/}/queue?token=${TOKEN}"
