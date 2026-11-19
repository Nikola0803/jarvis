#!/usr/bin/env bash
# Jarvis full deploy script -- run as root on the VPS
# Usage: bash deploy.sh
set -e

echo "=== Jarvis Deploy ==="

# 1. System deps
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git

# 2. Clone or pull
if [ -d /opt/jarvis/.git ]; then
  echo "Updating existing repo..."
  git -C /opt/jarvis pull
else
  echo "Cloning repo..."
  git clone https://github.com/Nikola0803/jarvis /opt/jarvis
fi

# 3. Virtualenv + deps
python3 -m venv /opt/jarvis/venv
/opt/jarvis/venv/bin/pip install -q --upgrade pip
/opt/jarvis/venv/bin/pip install -q -r /opt/jarvis/requirements.txt
echo "Dependencies installed."

# 4. Hermes user and dirs
id hermes &>/dev/null || useradd -r -s /bin/false -d /home/hermes hermes
mkdir -p /home/hermes/.hermes /etc/hermes
chown -R hermes:hermes /home/hermes/.hermes /etc/hermes
chmod 700 /etc/hermes /home/hermes/.hermes

# 5. Systemd service
cat > /etc/systemd/system/jarvis.service << 'EOF'
[Unit]
Description=Jarvis AI Assistant
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=hermes
WorkingDirectory=/opt/jarvis
ExecStart=/opt/jarvis/venv/bin/python run.py
Restart=always
RestartSec=15
Environment=HERMES_CONFIG=/etc/hermes/config.yaml
NoNewPrivileges=yes
ProtectSystem=strict
PrivateTmp=yes
ReadWritePaths=/home/hermes/.hermes /etc/hermes
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable jarvis

echo ""
echo "=== Deploy complete ==="
echo "Config expected at: /etc/hermes/config.yaml"
echo ""
if [ -f /etc/hermes/config.yaml ]; then
  echo "Config found. Starting Jarvis..."
  systemctl restart jarvis
  sleep 3
  systemctl status jarvis --no-pager
else
  echo "No config yet. Create /etc/hermes/config.yaml then run:"
  echo "  systemctl start jarvis"
  echo "  journalctl -u jarvis -f"
fi
