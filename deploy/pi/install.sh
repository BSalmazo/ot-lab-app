#!/usr/bin/env bash
# One-time (and idempotent) installation of the Liscere passive observer on a Raspberry Pi
# running Raspberry Pi OS (Debian Bookworm or newer). Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/LiscereSecurity/OT-Lab/main/deploy/pi/install.sh | sudo bash
#
# What it does: installs tshark, chrony and logrotate; lets a non-root user capture (dumpcap setcap
# via the wireshark group); creates the liscere system user and directories; installs the
# liscere-update command and runs it to fetch the latest release; installs and enables the
# systemd service. Re-running only refreshes what changed.
set -euo pipefail

REPO="${LISCERE_REPO:-LiscereSecurity/OT-Lab}"
RAW="https://raw.githubusercontent.com/$REPO/main/deploy/pi"
HOME_DIR=/opt/liscere

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "[install] packages"
export DEBIAN_FRONTEND=noninteractive
# Let dumpcap run with capture capabilities for members of the wireshark group (the Debian way).
echo "wireshark-common wireshark-common/install-setuid boolean true" | debconf-set-selections
apt-get update -qq
apt-get install -y -qq tshark chrony logrotate curl python3 python3-venv >/dev/null

echo "[install] user and directories"
id liscere >/dev/null 2>&1 || useradd --system --home-dir /var/lib/liscere --shell /usr/sbin/nologin liscere
usermod -aG wireshark liscere
install -d -o liscere -g liscere /var/lib/liscere /var/lib/liscere/runs /var/lib/liscere/checks
install -d "$HOME_DIR" /etc/liscere

echo "[install] commands and configuration"
curl -fsSL -o /usr/local/bin/liscere-update "$RAW/liscere-update"
chmod 0755 /usr/local/bin/liscere-update
curl -fsSL -o /usr/local/bin/bench-data-upload "$RAW/bench-data-upload"
chmod 0755 /usr/local/bin/bench-data-upload
if [ ! -f /etc/liscere/observe.env ]; then
  curl -fsSL -o /etc/liscere/observe.env "$RAW/observe.env"
  echo "[install] wrote /etc/liscere/observe.env with defaults (IFACE=eth0); edit it for this bench"
fi
curl -fsSL -o /etc/logrotate.d/liscere "$RAW/logrotate.conf"
curl -fsSL -o /etc/systemd/system/liscere-observe.service "$RAW/liscere-observe.service"

echo "[install] latest release"
LISCERE_SERVICE="" /usr/local/bin/liscere-update "${LISCERE_TAG:-}"
chown -R liscere:liscere "$HOME_DIR"
for cmd in liscere-observe liscere-probe liscere-ui; do
  ln -sf "$HOME_DIR/venv/bin/$cmd" "/usr/local/bin/$cmd"
done

echo "[install] time synchronisation"
# chrony uses the Debian pool by default. On a lab segment without internet, put the lab NTP source
# in /etc/chrony/conf.d/liscere.conf (e.g. "server 192.168.18.1 iburst") and restart chrony.
systemctl enable --now chrony >/dev/null 2>&1 || true

echo "[install] service"
systemctl daemon-reload
systemctl enable liscere-observe >/dev/null
systemctl restart liscere-observe
sleep 2
systemctl --no-pager --lines=5 status liscere-observe || true
echo
echo "[install] done: $("$HOME_DIR/venv/bin/liscere-observe" --version)"
echo "          logs: journalctl -u liscere-observe -f     runs: /var/lib/liscere/runs"
echo "          update: sudo liscere-update [vX.Y.Z]        rollback: sudo liscere-update --rollback"
