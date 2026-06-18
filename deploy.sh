#!/bin/bash
# =============================================================================
#  Internet Assist API — Azure deployment script
#  Usage: bash deploy.sh
#
#  Before running:
#  1. In .env, uncomment the AZURE lines and comment the LOCAL lines
#  2. Fill in SECRET_KEY and JWT_SECRET_KEY with strong random values
#  3. Run this script
# =============================================================================
set -euo pipefail

# ── Read secrets from .env ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Run from project root."
  exit 1
fi

# Load only non-comment, non-empty lines (quote values to preserve spaces)
eval "$(grep -v '^\s*#' .env | grep -v '^\s*$' | sed "s/^/export /; s/=\(.*\)/='\1'/")" 2>/dev/null || true

# ── Azure resource names ──────────────────────────────────────────────────────
RESOURCE_GROUP="IA"
LOCATION="centralus"
APP_NAME="internet-assist-api"
PLAN_NAME="ia-api-plan"
SQL_SERVER_NAME="ia-sql-server-121"   # existing server — set to your server name
SQL_DB_NAME="InternetAssist"

# ── Colours ───────────────────────────────────────────────────────────────────
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${CYAN}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }

# ── Validate required vars ────────────────────────────────────────────────────
for var in DATABASE_URL SECRET_KEY JWT_SECRET_KEY AI_API_KEY; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: $var is not set in .env"
    exit 1
  fi
done

# ── 1. Resource group ─────────────────────────────────────────────────────────
step "Resource group"
az group show --name "$RESOURCE_GROUP" &>/dev/null && ok "'$RESOURCE_GROUP' exists" || {
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
  ok "Created '$RESOURCE_GROUP'"
}

# ── 2. SQL Server + Firewall ──────────────────────────────────────────────────
step "Azure SQL Server"
az sql server show --name "$SQL_SERVER_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null && ok "Server '$SQL_SERVER_NAME' exists" || {
  # Extract password from DATABASE_URL: mssql+pymssql://user%40server:PASSWORD@host/db
  SQL_PASS=$(echo "$DATABASE_URL" | python3 -c "from urllib.parse import urlparse,unquote; u=urlparse(input()); print(unquote(u.password))")
  az sql server create \
    --name "$SQL_SERVER_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --admin-user "iaadmin" \
    --admin-password "$SQL_PASS" \
    --output none
  ok "Created SQL server '$SQL_SERVER_NAME'"
}

step "SQL Firewall — allow Azure services"
az sql server firewall-rule create \
  --resource-group "$RESOURCE_GROUP" --server "$SQL_SERVER_NAME" \
  --name "AllowAzureServices" \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 \
  --output none
ok "Firewall rule set"

step "SQL Database"
az sql db show --name "$SQL_DB_NAME" --server "$SQL_SERVER_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null && ok "Database '$SQL_DB_NAME' exists" || {
  az sql db create \
    --resource-group "$RESOURCE_GROUP" --server "$SQL_SERVER_NAME" \
    --name "$SQL_DB_NAME" --service-objective Basic --output none
  ok "Created database '$SQL_DB_NAME'"
}

# ── 3. App Service Plan ───────────────────────────────────────────────────────
step "App Service Plan"
az appservice plan show --name "$PLAN_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null && ok "Plan '$PLAN_NAME' exists" || {
  az appservice plan create \
    --name "$PLAN_NAME" --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" --sku B1 --is-linux --output none
  ok "Created App Service Plan '$PLAN_NAME' (B1 Linux)"
}

# ── 4. Web App ────────────────────────────────────────────────────────────────
step "Web App"
az webapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" &>/dev/null && ok "Web app '$APP_NAME' exists" || {
  az webapp create \
    --resource-group "$RESOURCE_GROUP" --plan "$PLAN_NAME" \
    --name "$APP_NAME" --runtime "PYTHON:3.12" --output none
  ok "Created web app '$APP_NAME'"
}

# ── 5. App Settings — push all vars from .env ─────────────────────────────────
step "App settings"
az webapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" --output none \
  --settings \
    APP_ENV="production" \
    DATABASE_URL="$DATABASE_URL" \
    SECRET_KEY="$SECRET_KEY" \
    JWT_SECRET_KEY="$JWT_SECRET_KEY" \
    CORS_ORIGINS="${CORS_ORIGINS:-https://${APP_NAME}.azurewebsites.net,https://internet-assist.lovable.app}" \
    AI_PROVIDER="${AI_PROVIDER:-gemini}" \
    AI_MODEL_NAME="${AI_MODEL_NAME:-gemini-flash-latest}" \
    AI_API_KEY="$AI_API_KEY" \
    INITIAL_ADMIN_EMAIL="${INITIAL_ADMIN_EMAIL:-admin@internetassist.co.uk}" \
    INITIAL_ADMIN_PASSWORD="${INITIAL_ADMIN_PASSWORD:-ChangeMe123!}" \
    PUBLIC_CONTACT_EMAIL="${PUBLIC_CONTACT_EMAIL:-enquiries@internetassist.co.uk}" \
    PUBLIC_CONTACT_PHONE="${PUBLIC_CONTACT_PHONE:-01621 840014}" \
    GRAPH_TENANT_ID="${GRAPH_TENANT_ID:-}" \
    GRAPH_CLIENT_ID="${GRAPH_CLIENT_ID:-}" \
    GRAPH_CLIENT_SECRET="${GRAPH_CLIENT_SECRET:-}" \
    GRAPH_SENDER="${GRAPH_SENDER:-}" \
    NOTIFY_EMAIL_1="${NOTIFY_EMAIL_1:-}" \
    NOTIFY_EMAIL_2="${NOTIFY_EMAIL_2:-}" \
    TICKET_API_URL="${TICKET_API_URL:-}" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true"
ok "App settings pushed from .env"

# ── 6. Startup command ────────────────────────────────────────────────────────
step "Startup command"
az webapp config set \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
  --startup-file "bash startup.sh" --output none
ok "Startup: bash startup.sh"

# ── 7. Zip & deploy ───────────────────────────────────────────────────────────
step "Building deployment package"
DEPLOY_ZIP="/tmp/ia-deploy.zip"
zip -r "$DEPLOY_ZIP" . \
  --exclude "*.pyc" \
  --exclude "*/__pycache__/*" \
  --exclude ".venv/*" \
  --exclude "venv/*" \
  --exclude ".env" \
  --exclude "instance/*" \
  --exclude "*.egg-info/*" \
  --exclude ".git/*" \
  -q
ok "Package built ($(du -sh $DEPLOY_ZIP | cut -f1))"

step "Deploying to Azure"
az webapp deploy \
  --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
  --src-path "$DEPLOY_ZIP" --type zip --async true --output none 2>&1 || true
ok "Deployment submitted — Azure is building (takes ~3 min)"

# ── 8. Summary ────────────────────────────────────────────────────────────────
APP_URL="https://${APP_NAME}.azurewebsites.net"
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Done!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "  App URL   : $APP_URL"
echo "  Health    : $APP_URL/healthz"
echo "  Docs      : $APP_URL/docs"
echo "  Admin     : $INITIAL_ADMIN_EMAIL / $INITIAL_ADMIN_PASSWORD"
echo ""
warn "Azure is still building. Wait ~3 min then check /healthz"
warn "After confirming live, keep CORS_ORIGINS set to both the Azure API and your frontend domain(s)."
echo ""
