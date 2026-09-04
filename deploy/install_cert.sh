#!/usr/bin/env bash
# ==============================================================================
# PrintHive Universal Client SSL Trust & Host Setup Script
# Works on macOS and Linux to trust PrintHive's certificate for PWA & HTTPS.
# ==============================================================================
set -euo pipefail

HOST="${1:-printhive.local.home}"
SERVER_IP="${2:-192.168.1.250}"
CERT_URL="http://${SERVER_IP}/cert"
TEMP_CERT="/tmp/printhive.crt"

echo "============================================================"
echo " PrintHive Client Setup: Trust SSL Certificate & Hosts"
echo "============================================================"
echo "Connecting to PrintHive server at ${SERVER_IP}..."

# 1. Download certificate over HTTP (avoids SSL warning on first bootstrap)
if curl -fsSL "${CERT_URL}" -o "${TEMP_CERT}"; then
    echo "✓ Certificate successfully retrieved from ${CERT_URL}."
else
    echo "Warning: Could not fetch from ${CERT_URL}, trying https://${SERVER_IP}/cert..."
    curl -kfsSL "https://${SERVER_IP}/cert" -o "${TEMP_CERT}"
    echo "✓ Certificate retrieved via fallback."
fi

# 2. Add /etc/hosts entries if missing
echo "Checking /etc/hosts for ${HOST} and orcaslicer.local.home..."
if ! grep -q "${HOST}" /etc/hosts 2>/dev/null; then
    echo "Adding host entries to /etc/hosts (requires sudo)..."
    echo -e "${SERVER_IP}  ${HOST} orcaslicer.local.home printhive-srv" | sudo tee -a /etc/hosts >/dev/null
    echo "✓ Added ${HOST} -> ${SERVER_IP} to /etc/hosts"
else
    echo "✓ Host entries already present in /etc/hosts"
fi

# 3. Trust certificate based on OS
OS="$(uname -s)"
case "${OS}" in
    Darwin)
        echo "Detected macOS. Adding PrintHive certificate to System Keychain..."
        sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "${TEMP_CERT}"
        echo "✓ PrintHive Certificate added and fully trusted in macOS System Keychain!"
        echo "✓ Chrome, Safari, and Edge will now show a valid, secure padlock on https://${HOST}/"
        ;;
    Linux)
        echo "Detected Linux. Installing certificate into system trust store..."
        if [ -d "/usr/local/share/ca-certificates" ]; then
            sudo cp "${TEMP_CERT}" /usr/local/share/ca-certificates/printhive.crt
            sudo update-ca-certificates
        elif [ -d "/etc/pki/ca-trust/source/anchors" ]; then
            sudo cp "${TEMP_CERT}" /etc/pki/ca-trust/source/anchors/printhive.crt
            sudo update-ca-trust
        fi
        echo "✓ Certificate installed into Linux trust store!"
        ;;
    *)
        echo "Unrecognized OS: ${OS}. Certificate saved at ${TEMP_CERT} for manual install."
        ;;
esac

echo ""
echo "============================================================"
echo " SETUP COMPLETE! 🎉"
echo "============================================================"
echo "Open https://${HOST}/ in your browser."
echo "1. On Chrome/Edge: Click the 'Install PrintHive' icon in the address bar."
echo "2. On Safari (Mac): File -> Add to Dock... to run as a standalone Mac app."
echo "3. On iPhone/iPad: Download profile at https://${HOST}/cert/printhive.mobileconfig"
echo "============================================================"
