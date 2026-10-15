#!/usr/bin/env python3
"""Build a 4-channel version of SOF's sof-hda-generic-4ch.tplg for the MateBook.

The stock topology limits the analog speaker PCM to 2 channels, so the codec
copies one stereo signal to both speaker outputs ("Speaker" and "Bass
Speaker"). Windows drives the same codec with 4 channels: woofers and
tweeters get separate signals. This script changes only:
  - PCM capabilities 'Playback 0' and 'Playback 31': channels_max 2 -> 4
  - the six playback buffers on that path: 768 -> 1536 bytes
  - the HDA codec link 'Analog Playback and Capture' channels (token1501): 2 -> 4
    (left at 2, the firmware frames a 4-channel stream as 2-channel: robotic
    sound. The headset-jack mic shares this link and may not work in 4-ch mode.)

Safety: it decodes the installed topology, repairs what `alsatplg --decode`
loses (negative TLV minimums, widget pipeline indices, BE hw_configs), and
refuses to continue unless recompiling the UNCHANGED text reproduces the
installed file byte for byte.

Usage: make_4ch_topology.py [SRC.tplg] OUT.tplg
"""
import os, re, struct, subprocess, sys, tempfile

SRC_DEFAULT = "/lib/firmware/intel/sof-tplg/sof-hda-generic-4ch.tplg"
CAPS = ("Playback 0", "Playback 31")
BUFFERS = ("BUF30.0", "BUF30.1", "BUF31.0", "BUF31.1", "BUF1.0", "BUF1.1")


def widget_index(blob):
    i, widx = 0, {}
    while i + 36 <= len(blob):
        magic, _, _, typ, size, _, psize, index, count = struct.unpack("<4sIIIIIIII", blob[i:i + 36])
        if magic != b"CoSA":
            raise SystemExit("not a topology file")
        if typ == 5:                                  # DAPM widget block
            q = i + size
            for _ in range(count):
                wsize = struct.unpack("<I", blob[q:q + 4])[0]
                name = blob[q + 8:q + 52].split(b"\0")[0].decode()
                nk = struct.unpack("<I", blob[q + 124:q + 128])[0]
                q += wsize + struct.unpack("<I", blob[q + wsize - 4:q + wsize])[0]
                widx[name] = index
                for _ in range(nk):
                    hsize = struct.unpack("<I", blob[q:q + 4])[0]
                    esize = struct.unpack("<I", blob[q + hsize:q + hsize + 4])[0]
                    q += esize + struct.unpack("<I", blob[q + esize - 4:q + esize])[0]
        i += size + psize
    return widx


def section(s, name):
    start = s.index(f"{name} {{")
    end = s.index("\nSection", start + 1)
    return start, end


def signed(hexstr):
    v = int(hexstr, 16)
    return v - (1 << 32) if v >= 1 << 31 else v


def repair(text, widx):
    # 1. negative TLV minimums are decoded as hex and dropped on recompile
    text = re.sub(r"min (0x[0-9a-fA-F]+)", lambda m: f"min {signed(m.group(1))}", text)
    # 2. widget pipeline indices
    a, b = section(text, "SectionWidget")
    sec = re.sub(r"\t'([^']+)' \{\n", lambda m: f"\t'{m.group(1)}' {{\n\t\tindex {widx[m.group(1)]}\n", text[a:b])
    text = text[:a] + sec + text[b:]
    # 3. back-end hw_configs (one per link, id only)
    a, b = section(text, "SectionBE")
    ids = re.findall(r"default_hw_conf_id (\d+)", text[a:b])
    sec = re.sub(r"(\t\tdefault_hw_conf_id (\d+)\n)",
                 lambda m: m.group(1) + f"\t\thw_configs [\n\t\t\t'hwcfg{m.group(2)}'\n\t\t]\n", text[a:b])
    hw = "SectionHWConfig {\n" + "".join(f"\t'hwcfg{i}' {{\n\t\tid {i}\n\t}}\n" for i in ids) + "}\n"
    return text[:a] + hw + sec + text[b:]


def edit(text):
    n = 0
    for cap in CAPS:
        text, k = re.subn(r"(\t'" + re.escape(cap) + r"' \{.*?channels_max )2(\n)", r"\g<1>4\2", text, count=1, flags=re.S)
        n += k
    for buf in BUFFERS:
        text, k = re.subn(r"(\t'" + re.escape(buf) + r":tuple0' \{.*?token100 )768(\n)", r"\g<1>1536\2", text, count=1, flags=re.S)
        n += k
    text, k = re.subn(r"(\t'Analog Playback and Capture:tuple0' \{.*?token1501 )2(\n)", r"\g<1>4\2",
                      text, count=1, flags=re.S)
    n += k
    if n != len(CAPS) + len(BUFFERS) + 1:
        raise SystemExit(f"expected {len(CAPS) + len(BUFFERS) + 1} edits, made {n}: topology layout differs, aborting")
    return text


def compile_(conf, out):
    subprocess.run(["alsatplg", "-c", conf, "-o", out], check=True, capture_output=True)


def main():
    args = sys.argv[1:]
    if len(args) not in (1, 2):
        raise SystemExit(__doc__)
    src, out = (SRC_DEFAULT, args[0]) if len(args) == 1 else args
    blob = open(src, "rb").read()
    with tempfile.TemporaryDirectory() as d:
        dec = os.path.join(d, "decoded.conf")
        subprocess.run(["alsatplg", "--decode", src, "-o", dec], check=True, capture_output=True)
        fixed = repair(open(dec).read(), widget_index(blob))
        open(os.path.join(d, "fixed.conf"), "w").write(fixed)
        compile_(os.path.join(d, "fixed.conf"), os.path.join(d, "roundtrip.tplg"))
        if open(os.path.join(d, "roundtrip.tplg"), "rb").read() != blob:
            raise SystemExit("round trip is not byte-identical to the source topology: aborting, nothing written")
        print("round trip verified byte-identical")
        open(os.path.join(d, "4ch.conf"), "w").write(edit(fixed))
        compile_(os.path.join(d, "4ch.conf"), out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
