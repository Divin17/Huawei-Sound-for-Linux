# Huawei MateBook speakers for Linux

Makes the six speakers of the **Huawei MateBook X Pro 2022 (MRGF-XX)** sound the
way Huawei tuned them, by loading Huawei's own crossover and EQ into the
laptop's smart amplifiers.

## The problem

The speakers are driven by three **Richtek RT9119** smart amps on I2C (ACPI
device `HWSP0001`):

| Amp | Bus | Address | Speakers |
|---|---|---|---|
| tweeter | I2C2 | `0x1B` | 2 tweeters |
| woofer 0 | I2C5 | `0x1A` | woofers |
| woofer 1 | I2C5 | `0x1B` | woofers |

The amps get their audio from the normal speaker output, so Linux already
plays through them. But on Windows, Huawei's `MultiPADriver.sys` also loads each
amp's DSP with a crossover and EQ:

- **tweeters:** 2nd-order high-pass at ~3 kHz, plus a notch at ~3 kHz
- **woofers:** 100 Hz high-pass (excursion protection), dips at 500 Hz and
  2 kHz, low-pass at ~4 kHz, plus left/right channel routing

No Linux driver does this, so every speaker plays the full range at flat EQ:
thin, harsh sound with tweeters wasting power on bass.

## What this does

`huawei-speakers` is a small root daemon that:

1. checks every 5 s whether the amps hold Huawei's tuning (one read-only I2C read per amp)
2. if not (at boot, after suspend, after an amp reset), plays 4 s of silence
   through the logged-in user's PipeWire session, because the amp DSP only
   accepts coefficients while it receives an audio clock
3. replays Huawei's register sequence ("mode 2": reset, config, crossover/EQ,
   power-on, unmute; 58 I2C writes) and reads everything back to verify it

The amps keep the tuning when audio stops; they lose it across suspend or power loss.

## Install

Requirements: PipeWire (`pw-play`), Python 3, systemd. No extra packages.

Huawei's tuning data is not included in this repo. The installer decodes it
from the Windows MultiPA driver's config files, which are on your Windows
partition if you dual-boot (`C:\Windows\System32\drivers\MULTIPA_DEV_0001_SUBSYS_19E53E59_*_RT9119*_config.dat`).
Mount the Windows partition, or copy those two files into a folder.

```bash
git clone <this repo> && cd huawei-matebook-speakers
./install.sh              # finds a mounted Windows partition automatically
./install.sh ~/multipa    # or: folder holding the two .dat files
```

It installs `/usr/local/bin/huawei-speakers`, the register data in
`/usr/local/share/huawei-speakers/`, loads `i2c-dev` at boot, and enables
`huawei-speakers.service`. You'll hear a short click when the tuning loads at
login and after waking from sleep.

## Control

```bash
sudo systemctl stop huawei-speakers      # stop watching (tuning stays until reboot/suspend)
sudo systemctl start huawei-speakers
sudo systemctl disable huawei-speakers   # don't start at boot
sudo huawei-speakers --status            # "tuned" or which registers differ
sudo huawei-speakers --once              # load the tuning right now
journalctl -u huawei-speakers            # log
```

To hear the untuned Linux default again, stop the service and reboot.

## Keep the volume sensible

Windows also runs Huawei's speaker-protection limiter (the SmartPA APO) in
software, and that isn't available here. The amps' own 100 Hz high-pass
protects the woofers from deep bass, but avoid sustained full-volume bass-heavy
audio, especially with extra bass boost from EasyEffects or similar.

## Data

The installer writes `data/*.seq`, the register sequences decoded from Huawei's
`MULTIPA_DEV_0001_SUBSYS_19E53E59_{TWEETER,WOOFER}_RT9119*_config.dat` files
that ship with the Windows MultiPA driver. They are git-ignored. To decode by hand:

```bash
python3 tools/decode_multipa.py MULTIPA_..._TWEETER_RT9119_config.dat > data/TWEETER_RT9119.seq
```

The format is documented in that script and in [docs/HARDWARE.md](docs/HARDWARE.md).

Only the RT9119 variant is supported. Huawei also fits FourSemi FS2119 amps
(addresses `0x69`/`0x6B`) in some units; check yours with
`sudo i2cdetect -y -r <bus>`.

## Uninstall

```bash
./uninstall.sh
```

## License

MIT, see [LICENSE](LICENSE). No Huawei files or data are included.
