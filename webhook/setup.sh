#!/bin/bash
# Setup script for PropertyTracker Telegram webhook
#
# Prerequisites:
#   1. Cloudflare account (free): https://dash.cloudflare.com/sign-up
#   2. Install wrangler: npm install -g wrangler
#   3. Login: wrangler login
#
# Usage:
#   cd webhook
#   bash setup.sh

set -e

# Check wrangler is installed
if ! command -v wrangler &> /dev/null; then
    echo "Error: wrangler not found. Install with: npm install -g wrangler"
    exit 1
fi

# Generate a random webhook secret
WEBHOOK_SECRET=$(openssl rand -hex 32)
echo "Generated webhook secret: $WEBHOOK_SECRET"

# Set secrets in Cloudflare Worker
echo ""
echo "=== Setting Cloudflare Worker secrets ==="
echo "You'll be prompted for each secret value."
echo ""

echo "Enter your Telegram bot token:"
wrangler secret put TELEGRAM_BOT_TOKEN

echo ""
echo "Setting webhook secret (auto-generated)..."
echo "$WEBHOOK_SECRET" | wrangler secret put WEBHOOK_SECRET

echo ""
echo "Enter a GitHub Personal Access Token (needs 'repo' scope):"
echo "Create one at: https://github.com/settings/tokens/new?scopes=repo"
wrangler secret put GITHUB_TOKEN

# Deploy the worker
echo ""
echo "=== Deploying Cloudflare Worker ==="
wrangler deploy

# Get the worker URL
WORKER_URL=$(wrangler deployments list --json 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    # Just construct from worker name
    print('')
except:
    print('')
" 2>/dev/null || echo "")

echo ""
echo "=== Worker deployed! ==="
echo ""
echo "Your worker URL is: https://propertytracker-webhook.<your-subdomain>.workers.dev"
echo "(Check the Cloudflare dashboard for the exact URL)"
echo ""

# Register webhook with Telegram
echo "Enter your Telegram bot token to register the webhook:"
read -r BOT_TOKEN

echo "Enter your Cloudflare Worker URL (e.g. https://propertytracker-webhook.xxx.workers.dev):"
read -r WORKER_URL

echo ""
echo "=== Registering Telegram webhook ==="
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
    -d "url=${WORKER_URL}" \
    -d "secret_token=${WEBHOOK_SECRET}" \
    -d "allowed_updates=[\"callback_query\"]" | python3 -m json.tool

echo ""
echo "=== Done! ==="
echo "Tap a review button in Telegram to test."
echo ""
echo "To check webhook status:"
echo "  curl https://api.telegram.org/bot\${BOT_TOKEN}/getWebhookInfo | python3 -m json.tool"
echo ""
echo "To remove webhook (go back to polling):"
echo "  curl https://api.telegram.org/bot\${BOT_TOKEN}/deleteWebhook"
