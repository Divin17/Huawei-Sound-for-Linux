#!/usr/bin/python3
"""Huawei MateBook speakers for Linux.

The MateBook X Pro (MRGF-XX) drives its six speakers through three Richtek
RT9119 smart amplifiers on I2C (ACPI HWSP0001): one tweeter amp on I2C2 and two
woofer amps on I2C5. They get their audio from the normal speaker output, but
on Linux nothing loads Huawei's tuning, so every speaker plays full range with
no crossover or EQ. This daemon loads Huawei's own register sequence
("mode 2": crossover, EQ, channel routing, power-on, unmute) into the amps.

The amps only accept DSP coefficients while they receive an audio clock, so a
few seconds of silence are played through the logged-in user's PipeWire
session during the load. The amps keep the coefficients when audio stops, but
lose them across suspend or power loss; the daemon checks every few seconds
and reloads when needed.

Amp gain: AMP_GAIN_DB in /etc/huawei-speakers.conf raises the amps' channel
volume (regs 0x08/0x09, 0.0625 dB/step, Huawei's 0x0180 = 0 dB) above Huawei's
startup value. Measured on an MRGF-XX: +6 dB with -0.1 dBFS peaks matched
Windows' loudness (65 dB(A) at 30 cm), and the amps start clipping around
+9 dB with -0.1 dBFS peaks (about +11.5 dB with peaks capped at -2.7 dBFS).
Gains up to SAFE_GAIN_DB were tested clean; up to MAX_GAIN_DB is allowed with a
warning, because clipping amps can damage the speakers over time. Keep
EasyEffects' peaks at about -2.7 dBFS when using a gain above 6 dB.

Usage:  huawei-speakers            run as a daemon (systemd service)
        huawei-speakers --status   show whether the amps are tuned
        huawei-speakers --once     load the tuning now and exit
        huawei-speakers --gain DB  set and apply the amp gain (0..MAX_GAIN_DB)
        huawei-speakers --woofer-mode 1|2   flat woofer amps (1) or Huawei's mode-2 EQ (2)
        huawei-speakers --tweeter-mode 1|2  flat tweeter amp (1, 4-channel mode only) or mode-2 EQ (2)

Woofer mode: WOOFER_MODE in /etc/huawei-speakers.conf. Mode 2 (default) loads
Huawei's full amp EQ, which includes a 100 Hz high-pass in the woofer amps.
Mode 1 is the same scene without the amp filters (flat woofers); use it only
with a preset that high-passes the woofer feed itself (Huawei's processing
uses a 4th-order 80 Hz high-pass and a 45 Hz notch, as Huawei-Exact does).

Tweeter mode: TWEETER_MODE. Mode 2 (default) loads the tweeter amp's 3 kHz
high-pass and 3.5-4 kHz notch. In 4-channel mode the software crossover
already high-passes the tweeter feed (2.4 kHz, 24 dB/oct), so the amp filters
cut 6-10 dB too much at 2-4 kHz (measured with the mic against Windows). Mode 1
drops them. Mode 1 is only honoured while the 4-channel SOF topology is loaded;
otherwise the tweeters would get the full-range signal, so mode 2 is used.
"""

import ctypes
import fcntl
import glob
import os
import pwd
import subprocess
import sys
import time
import wave

DATA_DIR = os.environ.get("HUAWEI_SPEAKERS_DATA", "/usr/local/share/huawei-speakers")
RUN_DIR = "/run/huawei-speakers"
POLL_S = 5
RETRY_S = 30

# bank -> ACPI I2C controller, chip addresses (chip0, chip1), sequence file
BANKS = [
    ("woofer", "I2C5", (0x1A, 0x1B), "WOOFER_RT9119.seq"),
    ("tweeter", "I2C2", (0x1B,), "TWEETER_RT9119.seq"),
]
CHECK_REG = 0x20  # first biquad; reads FLAT_BIQUAD after a reset
FLAT_BIQUAD = bytes([0x00, 0x80, 0x00, 0x00]) + bytes(16)   # b0 = 1.0, rest 0
CONF = "/etc/huawei-speakers.conf"
FOURCH_TPLG = "sof-hda-generic-4ch-matebook.tplg"
MAX_GAIN_DB = 12.0
SAFE_GAIN_DB = 8.0
VOLUME_REGS = (0x08, 0x09)
HUAWEI_VOLUME = 0x0180


def read_conf():
    conf = {}
    try:
        with open(CONF) as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, _, v = line.partition("=")
                    conf[k.strip()] = v.strip()
    except OSError:
        pass
    return conf


def write_conf(**updates):
    conf = read_conf(); conf.update({k: str(v) for k, v in updates.items()})
    with open(CONF, "w") as f:
        f.write(f"# huawei-speakers settings\n# AMP_GAIN_DB: 0..{MAX_GAIN_DB:g} dB above Huawei's startup amp volume\n"
                "# WOOFER_MODE: 2 = Huawei's amp EQ (incl. 100 Hz high-pass), 1 = flat woofer amps\n"
                "# TWEETER_MODE: 2 = Huawei's amp EQ (3 kHz high-pass), 1 = flat (4-channel mode only)\n")
        for k, v in conf.items():
            f.write(f"{k}={v}\n")


def read_gain():
    try:
        return max(0.0, min(MAX_GAIN_DB, float(read_conf().get("AMP_GAIN_DB", 0))))
    except ValueError:
        return 0.0


def woofer_mode():
    return 1 if read_conf().get("WOOFER_MODE", "2").strip() == "1" else 2


def fourch_active():
    """True while the 4-channel topology (separate, crossed-over tweeter feed) is loaded."""
    try:
        with open("/sys/module/snd_sof/parameters/tplg_filename") as f:
            return f.read().strip() == FOURCH_TPLG
    except OSError:
        return False


def tweeter_mode():
    want = 1 if read_conf().get("TWEETER_MODE", "2").strip() == "1" else 2
    return want if want == 2 or fourch_active() else 2


def scenes_for(name):
    """Huawei scenes to play for a bank: id = mode * 6 + position, then 0x19 = unmute."""
    if name == "woofer":
        m = woofer_mode()
        return (m * 6 + 1, m * 6 + 2, 0x19)
    return (tweeter_mode() * 6 + 3, 0x19)


def volume_bytes(gain_db):
    v = HUAWEI_VOLUME - round(gain_db * 16)
    return bytes([v >> 8, v & 0xFF])

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001


class I2cMsg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]


class I2cRdwr(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(I2cMsg)), ("nmsgs", ctypes.c_uint32)]


def log(*parts):
    print(*parts, flush=True)


class Bus:
    def __init__(self, acpi_name):
        self.fd = None
        for dev in glob.glob("/sys/bus/i2c/devices/i2c-*"):
            try:
                with open(f"{dev}/firmware_node/path") as f:
                    if f.read().strip().endswith("." + acpi_name):
                        self.fd = os.open("/dev/" + os.path.basename(dev), os.O_RDWR)
                        break
            except OSError:
                continue
        if self.fd is None:
            raise RuntimeError(f"I2C adapter {acpi_name} not found (is i2c-dev loaded?)")

    def _xfer(self, *msgs):
        arr = (I2cMsg * len(msgs))(*msgs)
        fcntl.ioctl(self.fd, I2C_RDWR, I2cRdwr(arr, len(msgs)))

    def write(self, addr, data):
        buf = (ctypes.c_uint8 * len(data))(*data)
        self._xfer(I2cMsg(addr, 0, len(data), buf))

    def read(self, addr, reg, n):
        wbuf = (ctypes.c_uint8 * 1)(reg)
        rbuf = (ctypes.c_uint8 * n)()
        self._xfer(I2cMsg(addr, 0, 1, wbuf), I2cMsg(addr, I2C_M_RD, n, rbuf))
        return bytes(rbuf)


def load_seq(path):
    """[(scene, chip, reg, delay_ms, data)] from a .seq file."""
    out = []
    with open(path) as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            if p[4] != "-":
                raise ValueError(f"{path}: read-modify-write records are not supported")
            out.append((int(p[0], 16), int(p[1]), int(p[2], 16), int(p[3]),
                        bytes(int(b, 16) for b in p[5:])))
    return out


class Amps:
    def __init__(self):
        self.banks = []
        for name, acpi, addrs, seqfile in BANKS:
            seq = load_seq(os.path.join(DATA_DIR, seqfile))
            self.banks.append((name, Bus(acpi), addrs, seq))

    def expected(self):
        """[(label, bus, addr, reg, data)] for every register the tuning sets."""
        out, vol = [], volume_bytes(read_gain())
        for name, bus, addrs, seq in self.banks:
            scenes = scenes_for(name)
            if not any(scene in scenes and reg == CHECK_REG for scene, _, reg, _, _ in seq):
                for chip in range(len(addrs)):       # flat mode: the reset leaves the biquads flat
                    out.append((f"{name}{chip}", bus, addrs[chip], CHECK_REG, FLAT_BIQUAD))
            for scene, chip, reg, _, data in seq:
                if scene in scenes and reg != 0x80:
                    if reg in VOLUME_REGS and data == volume_bytes(0):
                        data = vol
                    out.append((f"{name}{chip}", bus, addrs[chip], reg, data))
        return out

    def apply_gain(self):
        vol = volume_bytes(read_gain())
        for _, bus, addrs, _ in self.banks:
            for addr in addrs:
                for reg in VOLUME_REGS:
                    bus.write(addr, bytes([reg]) + vol)

    def tuned(self):
        try:
            return all(bus.read(addr, reg, len(data)) == data
                       for _, bus, addr, reg, data in self.expected() if reg == CHECK_REG)
        except OSError:
            return False

    def mismatches(self):
        bad = []
        for label, bus, addr, reg, data in self.expected():
            try:
                got = bus.read(addr, reg, len(data))
            except OSError as err:
                got = str(err).encode()
            if got != data:
                bad.append(f"{label}@{addr:#04x} reg {reg:#04x}")
        return bad

    def load(self):
        for name, bus, addrs, seq in self.banks:
            for want in scenes_for(name):
                for scene, chip, reg, delay, data in seq:
                    if scene == want:
                        bus.write(addrs[chip], bytes([reg]) + data)
                        if delay:
                            time.sleep(delay / 1000)
        self.apply_gain()


def user_session():
    """(username, runtime dir) of a user with a running PipeWire, or None."""
    for d in sorted(glob.glob("/run/user/*")):
        if os.path.exists(f"{d}/pipewire-0"):
            try:
                return pwd.getpwuid(int(os.path.basename(d))).pw_name, d
            except (KeyError, ValueError):
                continue
    return None


def silence_file(seconds=4):
    os.makedirs(RUN_DIR, mode=0o755, exist_ok=True)
    path = f"{RUN_DIR}/silence.wav"
    if not os.path.exists(path):
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(bytes(4 * 48000 * seconds))
        os.chmod(path, 0o644)
    return path


def tune(amps):
    """Play silence in the user's session and load the tuning. True on success."""
    session = user_session()
    if not session:
        return False
    user, runtime = session
    player = subprocess.Popen(
        ["runuser", "-u", user, "--", "env", f"XDG_RUNTIME_DIR={runtime}",
         "pw-play", silence_file()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)                  # let the stream start and clock the amps
        amps.load()
        time.sleep(0.5)
        bad = amps.mismatches()
    finally:
        player.terminate()
    if bad:
        log(f"tuning did not stick ({len(bad)} registers differ, e.g. {bad[0]}); will retry")
        return False
    log(f"speaker tuning loaded (clock from {user}'s audio session), amp gain +{read_gain():g} dB, "
        f"woofer mode {woofer_mode()}, tweeter mode {tweeter_mode()}")
    return True


def main():
    subprocess.run(["modprobe", "i2c-dev"], check=False)
    amps = Amps()

    if "--status" in sys.argv:
        bad = amps.mismatches()
        print("tuned" if not bad else f"NOT tuned: {len(bad)} registers differ ({', '.join(bad[:4])}...)")
        sys.exit(0 if not bad else 1)
    if "--gain" in sys.argv:
        gain = float(sys.argv[sys.argv.index("--gain") + 1])
        if not 0 <= gain <= MAX_GAIN_DB:
            sys.exit(f"gain must be 0..{MAX_GAIN_DB:g} dB")
        write_conf(AMP_GAIN_DB=f"{gain:g}")
        amps.apply_gain()
        print(f"amp gain set to +{gain:g} dB")
        if gain > SAFE_GAIN_DB:
            print(f"warning: above +{SAFE_GAIN_DB:g} dB the amps clip on loud peaks (crackling). "
                  "Clipping can damage the speakers over time; lower it if you hear any distortion.")
        sys.exit(0)
    if "--woofer-mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--woofer-mode") + 1]
        if mode not in ("1", "2"):
            sys.exit("woofer mode must be 1 (flat) or 2 (Huawei amp EQ)")
        write_conf(WOOFER_MODE=mode)
        ok = tune(amps)
        print(f"woofer mode {mode} " + ("loaded" if ok else "saved; the service will load it when audio can play"))
        sys.exit(0 if ok else 1)
    if "--tweeter-mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--tweeter-mode") + 1]
        if mode not in ("1", "2"):
            sys.exit("tweeter mode must be 1 (flat, 4-channel mode only) or 2 (Huawei amp EQ)")
        if mode == "1" and not fourch_active():
            sys.exit("tweeter mode 1 needs 4-channel mode (sudo huawei-4ch enable, then reboot): "
                     "without the software crossover the tweeters would get bass")
        write_conf(TWEETER_MODE=mode)
        ok = tune(amps)
        print(f"tweeter mode {mode} " + ("loaded" if ok else "saved; the service will load it when audio can play"))
        sys.exit(0 if ok else 1)
    if "--once" in sys.argv:
        sys.exit(0 if tune(amps) else 1)

    log("watching speaker amps")
    next_try = 0.0
    while True:
        if not amps.tuned() and time.monotonic() >= next_try:
            if not tune(amps):
                next_try = time.monotonic() + RETRY_S
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
