#!/usr/bin/env bash
# Turnkey IB Gateway + IBC headless installer for Ubuntu.
# Run on the server as sudo:  sudo ./setup_ibc.sh

set -euo pipefail

echo "================================================================="
echo "   IB Gateway + IBC Headless Service Installer"
echo "================================================================="

# 1. Install system prerequisites
echo "==> Installing system dependencies (xvfb, unzip, openjdk)..."
sudo apt-get update -y
sudo apt-get install -y xvfb unzip wget openjdk-11-jre-headless

# 2. Create guardrail user if not exists
if ! id -u guardrail >/dev/null 2>&1; then
    echo "==> Creating guardrail system user..."
    sudo useradd -m -s /bin/bash guardrail
fi

# 3. Download stable standalone IB Gateway installer
echo "==> Downloading IB Gateway installer..."
wget -c -q --show-progress https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh

# 4. Install IB Gateway (unattended/silent mode)
echo "==> Installing IB Gateway..."
chmod +x ibgateway-stable-standalone-linux-x64.sh
sudo cp ibgateway-stable-standalone-linux-x64.sh /home/guardrail/
sudo chown guardrail:guardrail /home/guardrail/ibgateway-stable-standalone-linux-x64.sh
# Run installer as guardrail user with working directory set to /home/guardrail
(cd /home/guardrail && sudo -u guardrail ./ibgateway-stable-standalone-linux-x64.sh -q)
# Clean up the installer from the user's home directory
sudo rm -f /home/guardrail/ibgateway-stable-standalone-linux-x64.sh

# 5. Find installed version
echo "==> Checking installed IB Gateway version..."
if [ -d /home/guardrail/ibgateway ]; then
    # Extract version from desktop file e.g. "IB Gateway 10.45.desktop" -> "1045"
    DESKTOP_FILE=$(ls /home/guardrail/ibgateway/ | grep "\.desktop" | head -n 1)
    GW_VERSION=$(echo "$DESKTOP_FILE" | grep -oE '[0-9]+\.[0-9]+' | sed 's/\.//g')
    echo "    Found version: ${GW_VERSION}"
else
    echo "Error: IB Gateway installation directory not found. Installation failed."
    exit 1
fi

# 6. Download and install IBC
echo "==> Downloading and installing IBC..."
wget -c -q --show-progress https://github.com/IbcAlpha/IBC/releases/download/3.23.0/IBCLinux-3.23.0.zip
sudo mkdir -p /opt/ibc
sudo unzip -o IBCLinux-3.23.0.zip -d /opt/ibc
sudo chmod -R 755 /opt/ibc

# 7. Prompt for credentials
echo ""
echo "-----------------------------------------------------------------"
echo "Please enter your IBKR Paper account credentials."
echo "Note: Use your paper account credentials, NOT your live account."
echo "-----------------------------------------------------------------"
read -p "Paper Login ID (Username): " ib_username
read -s -p "Paper Password: " ib_password
echo ""

# Write config.ini
echo "==> Creating config.ini..."
sudo tee /opt/ibc/config.ini > /dev/null <<EOF
IbLoginId=$ib_username
IbPassword=$ib_password
TradingMode=paper
IbDir=/home/guardrail/Jts
AcceptIncomingConnectionAction=accept
ReadOnlyApi=no
OverrideTwsApiPort=7497
AutoRestartTime=11:45 PM
EOF

sudo chmod 600 /opt/ibc/config.ini
sudo chown guardrail:guardrail /opt/ibc/config.ini

# 8. Setup systemd service
echo "==> Creating systemd service file..."
sudo tee /etc/systemd/system/ibc-gateway.service > /dev/null <<EOF
[Unit]
Description=IB Gateway via IBC (paper)
After=network-online.target
Wants=network-online.target

[Service]
User=guardrail
Environment=DISPLAY=:0
Environment=TWS_PATH=/home/guardrail/ibgateway
ExecStart=/usr/bin/xvfb-run -a /opt/ibc/gatewaystart.sh ${GW_VERSION} -g --ibc-ini=/opt/ibc/config.ini
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ibc-gateway

echo ""
echo "================================================================="
echo "   Installation completed successfully!"
echo "   Start the gateway service with:"
echo "       sudo systemctl start ibc-gateway"
echo "   Check status with:"
echo "       sudo systemctl status ibc-gateway"
echo "================================================================="
