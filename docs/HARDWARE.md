# Hardware notes: MateBook X Pro 2022 (MRGF-XX, Huawei codename "MorganF")

## Audio path

- HDA codec: Senary (ex-Conexant) SN6140, `14F1:20D0`, subsystem `19E5:326E`,
  driven by `snd_hda_codec_conexant` through SOF (`sof-hda-dsp`)
- Smart amps: 3 × Richtek RT9119, ACPI `HWSP0001` (`_SUB 19E53E59`), two ACPI nodes:
  - `\_SB.PC00.I2C2.HWSP`: I2C addresses `0x1B` (RT9119) / `0x69` (FS2119 alternate); tweeter amp
  - `\_SB.PC00.I2C5.HWSP`: `0x1A`, `0x1B` (RT9119) / `0x6B`, `0x69` (FS2119); 2 woofer amps
- The amps are fed from the normal speaker output. Verified by muting all
  three amps over I2C during playback, which silences the speakers completely.

## RT9119 registers (as used by Huawei)

All registers are 8-bit addressed; multi-byte registers are written as one burst
(`reg, b0, b1, …`).

| Reg | Size | Use |
|---|---|---|
| `0x80` | 1 | write `0x80` = soft reset (wait 2 ms) |
| `0x05` | 1 | `0x01` = running, `0x41` = power down |
| `0x07` | 2 | gain/format (`0x0230` tweeter, `0x0222` woofers) |
| `0x08`, `0x09` | 2 | channel volume: `0x0180` = on, `0x07FF` = mute |
| `0x12`, `0x13` | 8 | channel mixers / routing |
| `0x20`–`0x23` | 20 | biquads: b0, b1, b2, a1, a2 as 32-bit BE words holding 26-bit two's-complement Q23 (`0x800000` = 1.0); `y = b0·x + b1·x1 + b2·x2 + a1·y1 + a2·y2`, fs = 48 kHz |

**DSP coefficient registers (`0x12`, `0x13`, `0x20`+) only accept writes while
the amp receives an audio clock.** Without a stream they ACK the write and keep
their defaults (flat `0x800000 00…`). Simple registers work any time. The
coefficients survive when the stream stops.

## Huawei scenes (`*_config.dat`)

Scene id = mode × 6 + position (position 1 = woofer chip 0, 2 = woofer chip 1,
3 = tweeter):

| Scenes | Meaning |
|---|---|
| `0x00` | reset/init without power-on |
| `0x01`–`0x03`, `0x07`–`0x09` | modes 0/1: init + power on, little or no EQ |
| `0x0D`/`0x0E`/`0x0F` | **mode 2: init + full crossover/EQ + power on** (used here) |
| `0x13`–`0x18` | single-channel speaker tests (others muted) |
| `0x19` | all channels unmuted |
| `0x1B`/`0x1C`/`0x1D` | power off |
| `0x21` | register read-back list (diagnostics) |
