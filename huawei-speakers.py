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

# bank -> ACPI I2C controller, chip addresses (chip0, chip1), sequence file,
# scenes to play in order (Huawei mode 2 = full EQ, then 0x19 = unmute)
BANKS = [
    ("woofer", "I2C5", (0x1A, 0x1B), "WOOFER_RT9119.seq", (0x0D, 0x0E, 0x19)),
    ("tweeter", "I2C2", (0x1B,), "TWEETER_RT9119.seq", (0x0F, 0x19)),
]
CHECK_REG = 0x20  # first biquad; reads 0x800000 (flat) when untuned
CONF = "/etc/huawei-speakers.conf"
MAX_GAIN_DB = 12.0
SAFE_GAIN_DB = 8.0
VOLUME_REGS = (0x08, 0x09)
HUAWEI_VOLUME = 0x0180


def read_gain():
    try:
        with open(CONF) as f:
            for line in f:
                k, _, v = line.partition("=")
                if k.strip() == "AMP_GAIN_DB":
                    return max(0.0, min(MAX_GAIN_DB, float(v.strip())))
    except (OSError, ValueError):
        pass
    return 0.0


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
        for name, acpi, addrs, seqfile, scenes in BANKS:
            seq = load_seq(os.path.join(DATA_DIR, seqfile))
            self.banks.append((name, Bus(acpi), addrs, seq, scenes))

    def expected(self):
        """[(label, bus, addr, reg, data)] for every register the tuning sets."""
        out, vol = [], volume_bytes(read_gain())
        for name, bus, addrs, seq, scenes in self.banks:
            for scene, chip, reg, _, data in seq:
                if scene in scenes and reg != 0x80:
                    if reg in VOLUME_REGS and data == volume_bytes(0):
                        data = vol
                    out.append((f"{name}{chip}", bus, addrs[chip], reg, data))
        return out

    def apply_gain(self):
        vol = volume_bytes(read_gain())
        for _, bus, addrs, _, _ in self.banks:
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
        for name, bus, addrs, seq, scenes in self.banks:
            for want in scenes:
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
    log(f"speaker tuning loaded (clock from {user}'s audio session), amp gain +{read_gain():g} dB")
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
        with open(CONF, "w") as f:
            f.write(f"# amp gain above Huawei's startup level, 0..{MAX_GAIN_DB:g} dB\nAMP_GAIN_DB={gain:g}\n")
        amps.apply_gain()
        print(f"amp gain set to +{gain:g} dB")
        if gain > SAFE_GAIN_DB:
            print(f"warning: above +{SAFE_GAIN_DB:g} dB the amps clip on loud peaks (crackling). "
                  "Clipping can damage the speakers over time; lower it if you hear any distortion.")
        sys.exit(0)
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
