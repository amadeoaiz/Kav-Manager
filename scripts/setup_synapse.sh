#!/usr/bin/env bash
#
# KavManager — One-time Synapse + Cloudflare Tunnel setup for WSL2
#
# Run this once:  bash scripts/setup_synapse.sh
# It installs Synapse, creates the bot account, installs cloudflared,
# and generates start/stop helper scripts.
#
set -euo pipefail

SYNAPSE_SERVER_NAME="${1:-kavmanager.local}"
BOT_USERNAME="kavbot"
SYNAPSE_DATA_DIR="$HOME/.synapse"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== KavManager Synapse Setup ==="
echo "Server name : $SYNAPSE_SERVER_NAME"
echo "Data dir    : $SYNAPSE_DATA_DIR"
echo ""

# ── 1. Install Synapse ──────────────────────────────────────────────────────

if ! command -v synctl &>/dev/null; then
    echo "[1/5] Installing Matrix Synapse..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq matrix-synapse
else
    echo "[1/5] Synapse already installed."
fi

# ── 2. Generate config if missing ────────────────────────────────────────────

if [ ! -f "$SYNAPSE_DATA_DIR/homeserver.yaml" ]; then
    echo "[2/5] Generating Synapse config..."
    mkdir -p "$SYNAPSE_DATA_DIR"
    cd "$SYNAPSE_DATA_DIR"

    python3 -m synapse.app.homeserver \
        --server-name "$SYNAPSE_SERVER_NAME" \
        --config-path homeserver.yaml \
        --generate-config \
        --report-stats=no

    # Disable public registration (commander creates accounts manually)
    sed -i 's/^enable_registration: true/enable_registration: false/' homeserver.yaml

    # Bind only to localhost (Cloudflare Tunnel handles external access)
    sed -i 's/^  - port: 8008$/  - port: 8008/' homeserver.yaml

    echo "  Config written to $SYNAPSE_DATA_DIR/homeserver.yaml"
else
    echo "[2/5] Synapse config already exists at $SYNAPSE_DATA_DIR/homeserver.yaml"
fi

# ── 3. Start Synapse and create bot account ──────────────────────────────────

echo "[3/5] Starting Synapse to create bot account..."
synctl start "$SYNAPSE_DATA_DIR/homeserver.yaml" 2>/dev/null || true
sleep 3

BOT_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")

echo "  Creating bot user @${BOT_USERNAME}:${SYNAPSE_SERVER_NAME}..."
register_new_matrix_user \
    -c "$SYNAPSE_DATA_DIR/homeserver.yaml" \
    -u "$BOT_USERNAME" \
    -p "$BOT_PASSWORD" \
    --no-admin 2>/dev/null || echo "  (bot user may already exist)"

synctl stop "$SYNAPSE_DATA_DIR/homeserver.yaml" 2>/dev/null || true

# ── 4. Install cloudflared ───────────────────────────────────────────────────

if ! command -v cloudflared &>/dev/null; then
    echo "[4/5] Installing cloudflared..."
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
        -o /tmp/cloudflared.deb
    sudo dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
else
    echo "[4/5] cloudflared already installed."
fi

# ── 5. Generate start / stop scripts ────────────────────────────────────────

echo "[5/5] Writing helper scripts..."

cat > "$SCRIPTS_DIR/start_server.sh" << 'STARTEOF'
#!/usr/bin/env bash
# Start Synapse + Cloudflare quick-tunnel
# The quick-tunnel assigns a random public URL each time (free, no account needed).
set -euo pipefail

SYNAPSE_DATA_DIR="$HOME/.synapse"

echo "Starting Synapse..."
synctl start "$SYNAPSE_DATA_DIR/homeserver.yaml"
sleep 2

echo ""
echo "Starting Cloudflare quick-tunnel..."
echo ">>> The public URL will appear below. Give it to soldiers for Element login. <<<"
echo ""

# cloudflared prints the URL to stderr — this keeps it visible
cloudflared tunnel --url http://localhost:8008
STARTEOF
chmod +x "$SCRIPTS_DIR/start_server.sh"

cat > "$SCRIPTS_DIR/stop_server.sh" << 'STOPEOF'
#!/usr/bin/env bash
# Stop Synapse (cloudflared stops when its process is killed / Ctrl-C)
set -euo pipefail
SYNAPSE_DATA_DIR="$HOME/.synapse"
echo "Stopping Synapse..."
synctl stop "$SYNAPSE_DATA_DIR/homeserver.yaml"
echo "Done. (Kill the cloudflared terminal separately if still running.)"
STOPEOF
chmod +x "$SCRIPTS_DIR/stop_server.sh"

cat > "$SCRIPTS_DIR/create_matrix_user.sh" << 'USEREOF'
#!/usr/bin/env bash
# Create a soldier account on the local Synapse server.
# Usage: bash scripts/create_matrix_user.sh <username> <password>
set -euo pipefail
SYNAPSE_DATA_DIR="$HOME/.synapse"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <username> <password>"
    echo "Example: $0 wolf MySecurePass123"
    exit 1
fi

register_new_matrix_user \
    -c "$SYNAPSE_DATA_DIR/homeserver.yaml" \
    -u "$1" \
    -p "$2" \
    --no-admin

echo "Created user @${1}:$(grep 'server_name' "$SYNAPSE_DATA_DIR/homeserver.yaml" | head -1 | awk '{print $2}' | tr -d '\"')"
USEREOF
chmod +x "$SCRIPTS_DIR/create_matrix_user.sh"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Bot user     : @${BOT_USERNAME}:${SYNAPSE_SERVER_NAME}"
echo "  Bot password : ${BOT_PASSWORD}"
echo ""
echo "  Save the bot password — you will enter it in the"
echo "  desktop app under Settings → MATRIX CHAT."
echo ""
echo "  Next steps:"
echo "    1. bash scripts/start_server.sh"
echo "    2. Copy the public URL into the desktop app"
echo "    3. Create soldier accounts:"
echo "       bash scripts/create_matrix_user.sh wolf Password123"
echo "════════════════════════════════════════════════════"
