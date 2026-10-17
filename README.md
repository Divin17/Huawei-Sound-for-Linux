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

## Matching Windows' sound and loudness

The amps are only half of Huawei's sound. On Windows, Huawei's audio effects
(HWVE, Histen, SmartPA) also shape the signal, and Windows uses a gentler
volume curve. Both were measured on Windows (loopback, internal mic, volume API)
and reproduced here:

- **`huawei-volume-taper`** (user service, installed by `install.sh`) makes the
  volume slider follow Windows' measured curve (50% = -10.4 dB, 30% = -18 dB
  instead of Linux's -18 dB / -31 dB). It sets the speaker sink's master
  multiplier, so the slider value you see is unchanged. 100% is untouched.
- **`easyeffects/Huawei-Windows-Match.json`**, an EasyEffects preset fitted
  by measurement to Huawei's processing: the frequency response matches
  Windows within ±0.5 dB from 50 Hz to 16 kHz (+8 dB bass around 100 Hz, a
  steep cut below 55 Hz, mid scoop, crossover dip around 2 kHz), followed by
  2:1 compression and a limiter. Its output never exceeds what Windows sends the
  amps (about -13 dBFS RMS / -3 dBFS peak at full scale), which is the level
  Huawei's speaker protection allows. Copy it to
  `~/.local/share/easyeffects/output/` and select it for the Speaker output.
- **`easyeffects/Huawei-Histen-Bass.json`** (recommended, sounds best): a
  musical preset with Huawei-style level-dependent bass. A bass-only compressor
  (below 250 Hz) lifts quiet bass and holds loud bass back, giving about
  +20 / +9 / +3 dB at 100 Hz for quiet / medium / loud input (Windows measured
  +19.6 / +9.5 / +3.5). Peaks reach about -0.1 dBFS, about 2.6 dB above
  Windows' measured woofer peak, so avoid long full-volume bass-heavy playback.

### 4-channel mode (recommended): `Huawei-Normal`

`sudo huawei-4ch enable` (then reboot) gives the woofers and tweeters separate
feeds like Windows: a 3 kHz / 2.4 kHz crossover with a limiter per speaker pair
at Windows' measured ceilings (woofer -2.7 dBFS, tweeter -6.9 dBFS peak).
`sudo huawei-4ch disable` undoes it.

Use **`easyeffects/Huawei-Normal.json`** with it, together with
`sudo huawei-speakers --gain 8`. It is Huawei's measured chain and nothing
more: 3-band compressor (300 Hz / 3 kHz, bass release 30 ms) → 15-band voicing
EQ → limiter at -1 dBFS. Played with Windows' own test signals and compared
with Windows' loopback recordings, it matches within 1 dB per band at every
input level from -40 to -10 dBFS (frequency response 0.5 dB RMS), recovers bass
after a loud note at Windows' speed, and its peaks stay at Windows' per-speaker
ceilings.

**`easyeffects/Huawei-Bass.json`** is the same with about +3 dB more bass
at quiet and normal levels (+1.5 dB when loud): bass makeup 4.5 dB, ratio 4,
attack 3 ms. Pumping and crackle measure the same as Huawei-Normal; drum hits
are slightly softer (punch 5.6 vs 7.3 dB), because kick peaks can't rise above
the woofer ceiling. Switch back to Huawei-Normal any time.

Switch presets with **Ctrl+Alt+B** (cycles Normal → Bass → MaxBass and shows a
notification) or `huawei-sound 1|2|3`. The names are chosen so that none is the
start of another: EasyEffects' tray list mixes up presets whose names share a prefix
(e.g. `Huawei-Windows` and `Huawei-Windows-Bass`). Shortcut setup:

```bash
ln -sf "$PWD/huawei-sound" ~/.local/bin/huawei-sound
K=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/huawei-sound/
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$K']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K name 'Huawei sound'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K command "$HOME/.local/bin/huawei-sound"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K binding '<Control><Alt>b'
```

**`easyeffects/Huawei-MaxBass.json`** goes further: about +5 / +3.5 /
+1.5 dB bass at quiet / normal / loud levels (bass makeup 6.5 dB, ratio 4.5,
attack 3 ms). Kick pumping 2.4 dB, IMD -78 dB (inaudible), punch 4.1 dB.

### Amp gain and acoustic match

Measured with a phone sound meter (dB(A), 30 cm), Windows played the same test
signal about 7 dB louder than Linux at Huawei's startup amp volume, so Windows
evidently runs the amps hotter. `sudo huawei-speakers --gain 8` raises the
amps' channel volume by 8 dB (stored in `/etc/huawei-speakers.conf`, reapplied
after every reload). +8 dB was tested clean; up to +12 dB is allowed with a
warning. With peaks at -2.7 dBFS the amps start clipping (crackling) around
+11.5 dB, and clipping can damage the speakers over time. **With a gain above 6 dB, keep EasyEffects'
peaks at -2.7 dBFS** (all Huawei presets here use a maximizer output gain of
-2.6 dB). Presets that peak at 0 dBFS can make the amps crackle.

`easyeffects/Huawei-Acoustic-Match.json` is Histen-Bass plus a correction EQ
fitted with the laptop's microphone to the tonal balance Windows produced
through the same microphone (more bass, fewer mids).

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
