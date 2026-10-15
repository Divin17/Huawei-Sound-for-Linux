#!/usr/bin/env python3
"""Decode Huawei MultiPADriver *_config.dat files into .seq register sequences.

The .dat files ship with Huawei's Windows "MultiPA" smart-amp driver
(C:\\Windows\\System32\\drivers\\MULTIPA_DEV_0001_SUBSYS_19E53E59_*_config.dat).

Format, little-endian uint32:
  file   : sceneCount, scene[sceneCount]
  scene  : sceneId, chipMask, dwordLen, record[dwordLen / 7]
           chipMask 0x100 single chip, 0x1 chip0, 0x10 chip1,
           0x11 both (first half of the records chip0, second half chip1)
  record : reg, mask, value, delay_ms, op (0 write, 1 read), 1, byteLen
           byteLen > 4: the register continues in the following records as
           32-bit big-endian chunks; byteLen = bytes left from this chunk on

Output (.seq), one I2C write per line:
  <scene> <chip> <reg> <delay_ms> <mask|-> <byte> <byte> ...

Usage: decode_multipa.py FILE.dat > FILE.seq
"""
import struct
import sys


def decode(data):
    w = struct.unpack(f"<{len(data) // 4}I", data)
    i, lines = 1, []
    for _ in range(w[0]):
        sid, chips, dlen = w[i:i + 3]
        i += 3
        if dlen % 7:
            raise ValueError(f"scene {sid:#x}: length {dlen} is not a multiple of 7")
        nrec, buf = dlen // 7, b""
        for k in range(nrec):
            reg, mask, val, delay, op, _, blen = w[i:i + 7]
            i += 7
            chip = 1 if chips == 0x10 or (chips == 0x11 and k >= nrec / 2) else 0
            if op:                                   # read-back record, not a write
                continue
            n = min(4, blen)
            buf += val.to_bytes(4, "big")[4 - n:]
            if blen <= 4:
                m = "-" if mask == 0xFFFFFFFF else f"{mask & ((1 << 8 * len(buf)) - 1):#x}"
                lines.append(f"0x{sid:02X} {chip} 0x{reg:02X} {delay} {m} "
                             + " ".join(f"0x{b:02X}" for b in buf))
                buf = b""
    if i != len(w):
        raise ValueError(f"{len(w) - i} trailing dwords")
    return lines


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], "rb") as f:
        print("\n".join(decode(f.read())))
