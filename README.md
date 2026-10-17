# Huawei Sound for Linux

Makes the six speakers of the **Huawei MateBook X Pro 2022** sound the way they
do on Windows: Huawei's own amplifier tuning, separate woofer and tweeter feeds,
Huawei's sound processing as EasyEffects presets, and Windows' volume curve.
Everything is reversible.

## Tested on

| | |
|---|---|
| Laptop | **Huawei MateBook X Pro 2022**, model **MRGF-XX** (BIOS 1.39) |
| Amps | 3 × Richtek **RT9119** (ACPI `HWSP0001`) |
| OS | Ubuntu 26.04 LTS, kernel 7.0, GNOME |
| Audio | PipeWire 1.6, WirePlumber, EasyEffects 8.1, SOF (`skl_hda_dsp_generic`) |

Other MateBooks with RT9119 amps (`HWSP0001`) may work but are untested. Some
units ship FourSemi FS2119 amps instead (I2C addresses `0x69`/`0x6B`); those
are not supported. Check with `sudo i2cdetect -y -r <bus>`.

## The problem

The six speakers (2 tweeters, 4 woofers) are driven by three smart amps on I2C:

| Amp | Bus | Address | Speakers |
|---|---|---|---|
| tweeter | I2C2 | `0x1B` | 2 tweeters |
| woofer 0 | I2C5 | `0x1A` | woofers |
| woofer 1 | I2C5 | `0x1B` | woofers |

On Windows, Huawei's `MultiPADriver.sys` loads each amp's DSP with a crossover
and EQ, and Huawei's audio effects (SmartPA, Histen) split the sound into
separate woofer and tweeter signals with compression and voicing. Linux does
none of this, so every speaker plays the full range at flat EQ: thin, harsh,
quiet sound with the tweeters wasting power on bass.

## What this does

1. **`huawei-speakers`** (root service) loads Huawei's register sequences into
   the amps at boot, after suspend and after an amp reset. It checks every 5 s
   with one read-only I2C read per amp. The amp DSP only accepts coefficients
   while it receives an audio clock, so it plays a few seconds of silence through
   your PipeWire session while loading (you hear a short click).
2. **4-channel mode** (`huawei-4ch`): a patched SOF topology plus a PipeWire
   filter-chain give the woofers and tweeters separate feeds like Windows, with
   a crossover (woofers low-passed at 3 kHz, tweeters high-passed at 2.4 kHz,
   24 dB/oct) and a limiter per speaker pair.
3. **EasyEffects presets** reproduce Huawei's processing chain, measured on Windows.
4. **`huawei-volume-taper`** (user service) makes the volume slider follow
   Windows' measured curve (50% = −10.4 dB instead of −18 dB).
5. **`huawei-sound`** switches presets with **Ctrl+Alt+B**.

## Install

Requirements: PipeWire (`pw-play`, `pw-cli`), Python 3, systemd, EasyEffects 8,
and the ZaMaximX2 LADSPA plugin (`zam-plugins`).

Huawei's tuning data is not in this repo. The installer decodes it from the
Windows MultiPA driver's config files, which are on your Windows partition if
you dual-boot:
`C:\Windows\System32\drivers\MULTIPA_DEV_0001_SUBSYS_19E53E59_*_RT9119*_config.dat`.
Mount the Windows partition, or copy those two files into a folder.

```bash
sudo apt install easyeffects zam-plugins
git clone https://github.com/Divin17/Huawei-Sound-for-Linux.git
cd Huawei-Sound-for-Linux
./install.sh              # finds a mounted Windows partition automatically
./install.sh ~/multipa    # or: the folder holding the two .dat files
```

This installs `/usr/local/bin/huawei-speakers`, the register data in
`/usr/local/share/huawei-speakers/`, loads `i2c-dev` at boot, and enables
`huawei-speakers.service` and the volume-taper user service.

Then switch to 4-channel mode (recommended) and set the amp gain:

```bash
sudo ./huawei-4ch enable      # then reboot
sudo huawei-speakers --gain 5 # Windows-level loudness; up to 8 tested clean
```

Install the presets and the preset switcher:

```bash
cp easyeffects/Huawei-{Normal,Bass,MaxBass,Kick}.json ~/.local/share/easyeffects/output/
ln -sf "$PWD/huawei-sound" ~/.local/bin/huawei-sound
huawei-sound 1                # load Huawei-Normal
```

Bind Ctrl+Alt+B in GNOME:

```bash
K=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/huawei-sound/
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$K']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K name 'Huawei sound'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K command "$HOME/.local/bin/huawei-sound"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K binding '<Control><Alt>b'
```

(This replaces any other custom shortcuts; append `$K` to the existing list if you have some.)

## Presets (4-channel mode)

Switch with **Ctrl+Alt+B** (cycles and shows a notification) or `huawei-sound 1|2|3|4`.

| # | Preset | What it is |
|---|---|---|
| 1 | **Huawei-Normal** | Huawei's measured chain and nothing more: 3-band compressor (300 Hz / 3 kHz) → 15-band voicing EQ → limiter. Matches Windows within 1 dB per band from −40 to −10 dBFS input. |
| 2 | **Huawei-Bass** | About +3 dB more bass at quiet and normal levels (+1.5 dB when loud). |
| 3 | **Huawei-MaxBass** | About +5 / +3.5 / +1.5 dB bass at quiet / normal / loud levels. |
| 4 | **Huawei-Kick** | For strong bass kicks in loud music: the kick's attack passes the bass compressor. |

The preset names are chosen so that none is the start of another, because
EasyEffects' tray list mixes up presets whose names share a prefix.

The other presets in `easyeffects/` (Windows-Match, Histen-Bass, Acoustic-Match,
Exact, Punch) are earlier versions, made for 2-channel mode.

## How it was tuned

Everything was measured on Windows on the same laptop (4-channel loopback
capture, built-in mics, volume API) and compared with Linux using the same
test signals:

- **Signal per speaker pair:** the woofer and tweeter feeds match Windows within
  2–3 dB across the band, including Huawei's presence boost around 2.9 kHz.
- **Tweeter amp flat, tweeters inverted:** in 4-channel mode the tweeter amp runs
  without its own filter (`--tweeter-mode 1`, the default there). The amp filter
  left a +7 dB peak at 5–5.7 kHz. With the amp flat, woofers and tweeters
  cancelled 4–11 dB at 2.2–4.5 kHz, so the crossover inverts the tweeter feed.
  The service keeps the polarity matched to the tweeter mode.
- **Acoustic correction:** four peaking filters in the crossover (445 Hz +4,
  870 Hz −3.25, 5.4 kHz −4, 10.5 kHz +4 dB), fitted with the built-in mics
  against Windows' recording through the same mics. Deviation from Windows,
  300 Hz–14 kHz: 3.7 → 1.5 dB RMS. They apply to all presets.
- **Woofer headroom:** the woofers distort near full scale (at 150 Hz, the 3rd
  harmonic rises from −28 dB at −12 dBFS to −8 dB at −0.5 dBFS). The crossover
  keeps woofer peaks at −3 dBFS, which stopped audible crackle on bass-heavy
  music at full volume. Tweeter peaks stay at −6.9 dBFS, Windows' measured ceiling.
- **Loudness:** at amp gain +5 dB Linux matches Windows' loudness (phone sound
  meter, dB(A) at 30 cm). The amps start clipping around +9 dB with full-scale
  peaks. Up to +8 was tested clean with these presets, up to +12 is allowed with a
  warning.

Hardware details and the register format are in [docs/HARDWARE.md](docs/HARDWARE.md).

## Control and undo

```bash
sudo huawei-speakers --status            # "tuned" or which registers differ
sudo huawei-speakers --once              # load the tuning right now
sudo huawei-speakers --gain 0..12        # amp gain in dB above Huawei's startup level
sudo huawei-speakers --tweeter-mode 1|2  # 1 = flat (4-channel only), 2 = Huawei amp filter
sudo huawei-speakers --woofer-mode 1|2   # 2 = Huawei amp EQ incl. 100 Hz high-pass (keep 2)
journalctl -u huawei-speakers            # log
./huawei-4ch status                      # 2- or 4-channel mode
sudo ./huawei-4ch disable                # back to stock 2-channel (then reboot)
```

The crossover lives in `~/.config/pipewire/pipewire.conf.d/52-matebook-4ch-crossover.conf`.
Its filters can be changed live, for example to switch off the 10.5 kHz boost:

```bash
pw-cli s matebook_speakers Props '{ params = [ "eL4:Gain" 0 "eR4:Gain" 0 ] }'
```

Restarting PipeWire stops EasyEffects and cuts audio from running apps (Spotify must be fully
quit and reopened). Wired headphones are not handled in 4-channel mode.

## Keep the volume sensible

Windows also runs Huawei's speaker-protection limiter in software. That isn't
available here. The amps' own 100 Hz high-pass and the crossover limiters
protect the speakers, but avoid long full-volume bass-heavy playback,
especially with extra bass boost.

## Data

The installer writes `data/*.seq`, the register sequences decoded from Huawei's
`MULTIPA_DEV_0001_SUBSYS_19E53E59_{TWEETER,WOOFER}_RT9119*_config.dat` files. They are
git-ignored. To decode by hand:

```bash
python3 tools/decode_multipa.py MULTIPA_..._TWEETER_RT9119_config.dat > data/TWEETER_RT9119.seq
```

## Uninstall

```bash
sudo ./huawei-4ch disable   # if 4-channel mode is on, then reboot
./uninstall.sh
```

## License

MIT, see [LICENSE](LICENSE). No Huawei files or data are included.
