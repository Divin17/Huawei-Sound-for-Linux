#!/usr/bin/env bash
# Install the Huawei MateBook speaker tuning service (needs sudo).
set -euo pipefail
cd "$(dirname "$0")"
say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if ! ls /sys/bus/acpi/devices/ 2>/dev/null | grep -q '^HWSP0001'; then
    say "warning: no Huawei HWSP0001 amp device found - this is for the MateBook X Pro (MRGF-XX)."
    read -rp "Continue anyway? [y/N] " a; [[ $a == [yY] ]] || exit 1
fi
command -v pw-play >/dev/null || { say "pw-play (PipeWire) is required"; exit 1; }

# Huawei's register data is not distributed with this repo: decode it from the
# MultiPA driver's config files (Windows partition or a folder you give).
if ! [[ -s data/TWEETER_RT9119.seq && -s data/WOOFER_RT9119.seq ]]; then
    dirs=("${1:-}" /run/media/*/*/Windows/System32/drivers /media/*/*/Windows/System32/drivers
          /mnt/*/Windows/System32/drivers /mnt/Windows/System32/drivers)
    tw= wo=
    for d in "${dirs[@]}"; do
        [[ -n $d && -d $d ]] || continue
        tw=$(find "$d" -maxdepth 3 -iname 'MULTIPA_DEV_0001_SUBSYS_19E53E59_TWEETER_RT9119_config.dat' -print -quit 2>/dev/null)
        wo=$(find "$d" -maxdepth 3 -iname 'MULTIPA_DEV_0001_SUBSYS_19E53E59_WOOFER_RT9119_RT9119_config.dat' -print -quit 2>/dev/null)
        [[ -n $tw && -n $wo ]] && break
    done
    if [[ -z $tw || -z $wo ]]; then
        say "Huawei amp config files not found."
        echo "    Mount your Windows partition, or copy these two files from"
        echo "    C:\\Windows\\System32\\drivers\\ into a folder and run: ./install.sh <folder>"
        echo "      MULTIPA_DEV_0001_SUBSYS_19E53E59_TWEETER_RT9119_config.dat"
        echo "      MULTIPA_DEV_0001_SUBSYS_19E53E59_WOOFER_RT9119_RT9119_config.dat"
        exit 1
    fi
    say "Decoding Huawei amp config from $(dirname "$tw")"
    mkdir -p data
    python3 tools/decode_multipa.py "$tw" > data/TWEETER_RT9119.seq
    python3 tools/decode_multipa.py "$wo" > data/WOOFER_RT9119.seq
fi

say "Installing (sudo)"
sudo install -Dm 755 huawei-speakers.py /usr/local/bin/huawei-speakers
sudo install -Dm 644 -t /usr/local/share/huawei-speakers data/*.seq
sudo install -Dm 644 systemd/huawei-speakers.service /etc/systemd/system/huawei-speakers.service
echo i2c-dev | sudo tee /etc/modules-load.d/huawei-speakers.conf >/dev/null
sudo modprobe i2c-dev
sudo systemctl daemon-reload
sudo systemctl enable --now huawei-speakers.service

say "Installing the Windows volume curve (user service)"
install -Dm 755 volume-taper.py "$HOME/.local/bin/huawei-volume-taper"
install -Dm 644 data/windows_volume_curve.csv "$HOME/.local/share/huawei-speakers/windows_volume_curve.csv"
install -Dm 644 systemd/huawei-volume-taper.service "$HOME/.config/systemd/user/huawei-volume-taper.service"
systemctl --user daemon-reload
systemctl --user enable --now huawei-volume-taper.service

sleep 8
sudo huawei-speakers --status || true
say "Done. Control with: sudo systemctl start|stop|status huawei-speakers"
