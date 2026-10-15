#!/usr/bin/python3
"""Windows volume curve for the MateBook speakers.

GNOME/PipeWire map the volume slider cubically (50% = -18 dB, 30% = -31 dB),
while Windows uses its own taper (50% = -10.4 dB, 30% = -18 dB), so on Linux
the lower half of the slider is much quieter than on Windows.

This user daemon watches the speaker sink and sets its master volume
multiplier (the node's "volume" Prop, which the slider does not show) so that
slider position x gives exactly the attenuation Windows applies at x. It only
ever boosts towards Windows' level and never past 0 dB: at 100% nothing
changes, and the final gain is always <= 1.

Usage: huawei-volume-taper [--debug]
"""

import bisect
import json
import os
import signal
import subprocess
import sys
import time

SINK = os.environ.get(
    "HUAWEI_SPEAKER_SINK",
    "alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Speaker__sink")
CURVE = os.environ.get("HUAWEI_VOLUME_CURVE",
                       "/usr/local/share/huawei-speakers/windows_volume_curve.csv")
DEBUG = "--debug" in sys.argv
# With software volume (WirePlumber api.alsa.soft-mixer) PipeWire no longer
# manages these, so a stale value restored at boot would silently cap the level.
HW_CONTROLS = ("Master", "Speaker", "Bass Speaker")
HW_CHECK_S = 5


def log(*parts):
    print(*parts, flush=True)


def load_curve(path):
    pts = []
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            s, db = line.split(",")
            pts.append((float(s), float(db)))
    pts.sort()
    return [p[0] for p in pts], [p[1] for p in pts]


XS, DBS = load_curve(CURVE)


def windows_amplitude(x):
    """Linear gain Windows applies at slider position x (0..1)."""
    if x <= XS[0]:
        return 0.0
    if x >= XS[-1]:
        return 10 ** (DBS[-1] / 20)
    i = bisect.bisect_right(XS, x)
    x0, x1, d0, d1 = XS[i - 1], XS[i], DBS[i - 1], DBS[i]
    return 10 ** ((d0 + (d1 - d0) * (x - x0) / (x1 - x0)) / 20)


def multiplier(channel_volumes):
    """Master multiplier turning PipeWire's cubic level into Windows' level."""
    cv = sum(channel_volumes) / len(channel_volumes)
    if cv <= 0:
        return 1.0
    x = cv ** (1 / 3)                    # slider position as GNOME shows it
    return max(1.0, min(windows_amplitude(x) / cv, 1000.0))


def set_volume(node_id, value):
    subprocess.run(["pw-cli", "set-param", str(node_id), "Props", f"{{ volume: {value:.6f} }}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def hw_full(card):
    """Keep the codec's hardware speaker controls at 0 dB, unmuted."""
    for ctl in HW_CONTROLS:
        subprocess.run(["amixer", "-c", str(card), "-q", "set", ctl, "100%", "unmute"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def objects(stream):
    """Yield objects from `pw-dump --monitor`, which prints a JSON array per update."""
    buf = []
    for line in iter(stream.readline, ""):
        buf.append(line)
        if not line.startswith("]"):     # each update ends with "]" in column 0
            continue
        try:
            arr = json.loads("".join(buf))
        except json.JSONDecodeError:
            continue                     # not the outer array's end yet
        buf = []
        yield from (arr if isinstance(arr, list) else [arr])


def main():
    node_id, applied, hw_checked = None, {}, 0.0
    card = int(os.environ.get("HUAWEI_ALSA_CARD", "0"))   # also used when SINK is a virtual sink
    dump = subprocess.Popen(["stdbuf", "-oL", "pw-dump", "--monitor", "--no-colors"],
                            stdout=subprocess.PIPE, text=True)

    def stop(*_):
        if node_id is not None:
            set_volume(node_id, 1.0)
        dump.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(f"applying the Windows volume curve to {SINK}")

    for obj in objects(dump.stdout):
        info = obj.get("info")
        if info is None:                 # object removed
            if obj.get("id") == node_id:
                node_id = None
            continue
        props = info.get("props") or {}
        if props.get("node.name") == SINK:
            if node_id != obj["id"]:
                hw_checked = 0.0         # new or re-created sink: reset hardware now
            node_id = obj["id"]
            card = props.get("api.alsa.pcm.card", card)
        if obj.get("id") != node_id:
            continue
        if card is not None and time.monotonic() - hw_checked > HW_CHECK_S:
            hw_full(card)
            hw_checked = time.monotonic()
        for p in (info.get("params") or {}).get("Props") or []:
            cv = p.get("channelVolumes")
            if not cv:
                continue
            want = multiplier(cv)
            have = p.get("volume", 1.0)
            if abs(want - have) / want > 0.003 and applied.get(node_id) != round(want, 4):
                set_volume(node_id, want)
                applied[node_id] = round(want, 4)
                if DEBUG:
                    x = (sum(cv) / len(cv)) ** (1 / 3)
                    log(f"slider {x:5.1%}: multiplier {want:7.3f}")


if __name__ == "__main__":
    main()
