#!/usr/bin/env bash
# Remove the speaker tuning service. The amps keep the tuning until the next
# reboot or suspend, then return to Linux's untuned default.
set -euo pipefail
sudo systemctl disable --now huawei-speakers.service 2>/dev/null || true
sudo rm -f /usr/local/bin/huawei-speakers /etc/systemd/system/huawei-speakers.service \
           /etc/modules-load.d/huawei-speakers.conf
sudo rm -rf /usr/local/share/huawei-speakers
sudo systemctl daemon-reload
echo "Uninstalled."
