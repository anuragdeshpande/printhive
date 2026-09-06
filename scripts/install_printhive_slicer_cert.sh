#!/usr/bin/env bash
#
# install_printhive_slicer_cert.sh
#
# Downloads and installs PrintHive's Virtual Printer Root CA certificate
# into macOS Keychain (or Linux system trust store) so OrcaSlicer and
# BambuStudio can securely connect and bind to virtual printers.
#

set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

PRINTHIVE_URL="${PRINTHIVE_URL:-https://192.168.1.250}"
API_TOKEN="${PRINTHIVE_TOKEN:-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJyb290IiwiZXhwIjoxNzg4NjE0NzM1LCJqdGkiOiI2NTI4ZTM3OTEyN2Q0NWE0ZTMxMDNmMGQwNjc5MDk5MyIsImlhdCI6MTc4ODUyODMzNX0.FK7i6DEyTkSdShOWQuTTtIQ6lv_wghh0frxlxSVCPrk}"
CERT_FILE=""
TMP_CERT="/tmp/printhive-ca.crt"

usage() {
    echo -e "${BOLD}Usage:${NC} $(basename "$0") [OPTIONS]\n"
    echo -e "Install PrintHive Virtual Printer CA Certificate to trust store for OrcaSlicer / BambuStudio.\n"
    echo -e "${BOLD}Options:${NC}"
    echo -e "  -u, --url URL        PrintHive base URL (default: $PRINTHIVE_URL)"
    echo -e "  -t, --token TOKEN    API Bearer token for PrintHive authentication"
    echo -e "  -f, --file FILE      Path to local certificate PEM file (skip network download)"
    echo -e "  -h, --help           Show this help message\n"
    exit 0
}

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--url)
            PRINTHIVE_URL="$2"
            shift 2
            ;;
        -t|--token)
            API_TOKEN="$2"
            shift 2
            ;;
        -f|--file)
            CERT_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Unknown argument: $1${NC}"
            usage
            ;;
    esac
done

echo -e "${BOLD}${BLUE}=== PrintHive Slicer Certificate Installer ===${NC}\n"

# Step 1: Obtain the Certificate
if [[ -n "$CERT_FILE" ]]; then
    if [[ ! -f "$CERT_FILE" ]]; then
        echo -e "${RED}Error: Certificate file not found: $CERT_FILE${NC}"
        exit 1
    fi
    echo -e "Using local certificate file: ${BOLD}$CERT_FILE${NC}"
    TARGET_CERT="$CERT_FILE"
else
    echo -e "Fetching CA certificate from: ${BOLD}${PRINTHIVE_URL}/api/v1/virtual-printers/ca-certificate${NC}..."
    
    AUTH_HEADER=()
    if [[ -n "$API_TOKEN" ]]; then
        AUTH_HEADER=(-H "Authorization: Bearer ${API_TOKEN}")
    fi

    HTTP_RESPONSE=$(curl -k -s -w "\n%{http_code}" "${AUTH_HEADER[@]}" "${PRINTHIVE_URL}/api/v1/virtual-printers/ca-certificate" || true)
    HTTP_CODE=$(echo "$HTTP_RESPONSE" | tail -n 1)
    HTTP_BODY=$(echo "$HTTP_RESPONSE" | sed '$d')

    if [[ "$HTTP_CODE" -ne 200 ]]; then
        echo -e "${YELLOW}Could not fetch via API (HTTP $HTTP_CODE). Checking if /tmp/printhive-ca.crt exists...${NC}"
        if [[ -f "$TMP_CERT" ]]; then
            echo -e "${GREEN}Found existing $TMP_CERT! Using it.${NC}"
            TARGET_CERT="$TMP_CERT"
        else
            echo -e "${RED}Failed to download certificate (HTTP $HTTP_CODE):${NC}"
            echo "$HTTP_BODY"
            echo -e "${YELLOW}Tip: You can pass a downloaded certificate using --file <path_to_cert.crt>${NC}"
            exit 1
        fi
    else
        # Extract PEM from JSON response (supports jq or python)
        if command -v jq >/dev/null 2>&1; then
            echo "$HTTP_BODY" | jq -r '.pem // empty' > "$TMP_CERT"
        else
            python3 -c "import sys, json; data=json.loads(sys.stdin.read()); print(data.get('pem', ''))" <<< "$HTTP_BODY" > "$TMP_CERT"
        fi

        if [[ ! -s "$TMP_CERT" ]]; then
            echo -e "${RED}Error: Received empty certificate from API.${NC}"
            exit 1
        fi
        echo -e "${GREEN}✓ Certificate successfully fetched.${NC}"
        TARGET_CERT="$TMP_CERT"
    fi
fi

# Step 2: Validate Certificate
if ! openssl x509 -in "$TARGET_CERT" -noout 2>/dev/null; then
    echo -e "${RED}Error: Invalid X509 certificate in $TARGET_CERT.${NC}"
    exit 1
fi

CERT_SUBJECT=$(openssl x509 -in "$TARGET_CERT" -noout -subject | sed 's/subject=//')
CERT_ISSUER=$(openssl x509 -in "$TARGET_CERT" -noout -issuer | sed 's/issuer=//')
CERT_FINGERPRINT=$(openssl x509 -in "$TARGET_CERT" -noout -fingerprint -sha256 | sed 's/SHA256 Fingerprint=//')
CERT_EXPIRY=$(openssl x509 -in "$TARGET_CERT" -noout -enddate | sed 's/notAfter=//')

echo -e "\n${BOLD}Certificate Information:${NC}"
echo -e "  Subject:     ${BOLD}$CERT_SUBJECT${NC}"
echo -e "  Issuer:      ${BOLD}$CERT_ISSUER${NC}"
echo -e "  Expires:     $CERT_EXPIRY"
echo -e "  Fingerprint: ${BLUE}$CERT_FINGERPRINT${NC}"

# Step 3: Install into OS Trust Store
OS_NAME=$(uname -s)
echo -e "\n${BOLD}Installing into system trust store ($OS_NAME)...${NC}"

if [[ "$OS_NAME" == "Darwin" ]]; then
    # macOS Keychain
    echo -e "Adding certificate to ${BOLD}macOS System Keychain${NC}..."
    echo -e "${YELLOW}(Administrator password / Touch ID required)${NC}"

    if sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$TARGET_CERT"; then
        echo -e "${GREEN}✓ Successfully added and trusted in System Keychain!${NC}"
    else
        echo -e "${YELLOW}System Keychain install failed, attempting User Login Keychain...${NC}"
        security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$TARGET_CERT"
        echo -e "${GREEN}✓ Added to User Login Keychain.${NC}"
    fi

elif [[ "$OS_NAME" == "Linux" ]]; then
    # Linux ca-certificates
    if [[ -d "/usr/local/share/ca-certificates" ]]; then
        sudo cp "$TARGET_CERT" /usr/local/share/ca-certificates/printhive-virtual-printer-ca.crt
        sudo update-ca-certificates
        echo -e "${GREEN}✓ Added to Debian/Ubuntu CA certificates.${NC}"
    elif [[ -d "/etc/ca-certificates/trust-source/anchors" ]]; then
        sudo cp "$TARGET_CERT" /etc/ca-certificates/trust-source/anchors/printhive-virtual-printer-ca.crt
        sudo update-ca-trust
        echo -e "${GREEN}✓ Added to Arch/RHEL CA trust anchors.${NC}"
    fi
else
    echo -e "${YELLOW}Unsupported OS: $OS_NAME. Please import $TARGET_CERT manually.${NC}"
    exit 1
fi

# Step 4: Display Printer Binding Info
echo -e "\n${BOLD}${BLUE}=== Virtual Printers Configuration ===${NC}"
if [[ -n "$API_TOKEN" ]]; then
    PRINTERS_JSON=$(curl -k -s -H "Authorization: Bearer ${API_TOKEN}" "${PRINTHIVE_URL}/api/v1/virtual-printers" 2>/dev/null || true)
    if [[ -n "$PRINTERS_JSON" ]] && command -v jq >/dev/null 2>&1; then
        echo "$PRINTERS_JSON" | jq -r '.printers[] | "\(.name):\n  Model: \(.model_name) (\(.model))\n  IP: \(.bind_ip)\n  Serial: \(.serial)\n  Mode: \(.mode)\n"'
    fi
fi

# Check if OrcaSlicer is currently running
if pgrep -x "OrcaSlicer" >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  OrcaSlicer is currently running.${NC}"
    echo -e "${YELLOW}Please RESTART OrcaSlicer so it reloads the updated system certificate trust store.${NC}\n"
else
    echo -e "${GREEN}✓ OrcaSlicer is ready to start.${NC}\n"
fi

echo -e "${BOLD}${GREEN}Done! Your system now trusts PrintHive Virtual Printers.${NC}"
