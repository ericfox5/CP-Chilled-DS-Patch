"""Patch Club Penguin: Elite Penguin Force (DS, US "CLPE") so its online features talk to CP Chilled.

What it changes (documented in the main CP Chilled project, docs/DS.md):
  * ARM9 (Nintendo DWC-DL library): the NAS auth URL (x8 incl. test/dev variants) and the connection-test URL
  * overlay 0: the Club Penguin "console" URL (login / coin upload / poll / newsletter / missions)
  * DGamer/GameData/dgamer.xml: the DGamer dynamic-config URL
All become plain-http URLs on our host. Every replacement is the same length or shorter (NUL padded), so no
code moves; the ARM9 and overlay 0 are BLZ-recompressed by ndspy. The source ROM must be the user's own dump;
nothing ROM-derived is committed to the repo.

Run:  python tools/patch-epf-rom.py --src "Club Penguin - Elite Penguin Force.nds"      # writes "<name> (CP Chilled).nds"
      python tools/patch-epf-rom.py --src dump.nds --out out.nds --base http://your-host/prefix   # self-hosted server
Requires: python -m pip install ndspy
"""
import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import epflib  # noqa: E402

DEFAULT_SRC = ''            # your own dump, see --src
DEFAULT_OUT = ''            # default: '<dump name> (CP Chilled).nds' next to the dump
DEFAULT_BASE = 'http://ds.cpchilled.com'

# (original NUL-terminated string, path appended to --base, expected count, description)
ARM9_RULES = [
    (b'https://nas.nintendowifi.net/ac', '/ac', 5, 'NAS auth'),
    (b'https://nas.test.nintendowifi.net/ac', '/ac', 2, 'NAS auth (test)'),
    (b'https://nas.dev.nintendowifi.net/ac', '/ac', 1, 'NAS auth (dev)'),
    (b'http://conntest.nintendowifi.net/', '/', 1, 'connection test'),
]
OV0_RULES = [
    (b'https://console.clubpenguin.com/submit_uk.php', '/submit_uk.php', 1, 'Club Penguin console'),
]
XML_OLD = b'https://home.disney.go.com'
LEFTOVERS = (b'nintendowifi.net', b'clubpenguin.com', b'disney.go.com')


def replace_padded(buf: bytearray, old: bytes, new: bytes) -> int:
    """Overwrite every NUL-terminated `old` with `new` plus NUL padding. Returns the number of replacements."""
    if len(new) > len(old):
        raise SystemExit(f'replacement {new!r} is longer than {old!r} ({len(new)} > {len(old)})')
    n = 0
    for m in list(re.finditer(re.escape(old) + rb'\x00', bytes(buf))):
        buf[m.start():m.start() + len(old)] = new + b'\x00' * (len(old) - len(new))
        n += 1
    return n


def apply_rules(buf: bytearray, base: str, rules, label: str):
    for old, path, expected, what in rules:
        n = replace_padded(buf, old, (base + path).encode('ascii'))
        if n != expected:
            raise SystemExit(f'{label}: expected {expected} x {what} ({old.decode()}), found {n}')
        print(f'  {label}: {what:20s} {old.decode():46s} -> {base + path}  (x{n})')


def check_leftovers(blob: bytes, label: str):
    for needle in LEFTOVERS:
        if needle in blob:
            raise SystemExit(f'self-check failed: {needle.decode()} still present in {label}')


def self_check(out: Path, base: str):
    chk = epflib.Rom.from_file(out)
    a9 = chk.arm9()
    if len(a9) != epflib.ARM9_DECOMPRESSED_SIZE:
        raise SystemExit('self-check: ARM9 does not decompress to the expected size')
    check_leftovers(a9, 'ARM9')
    nas = len(epflib.find_all(a9, (base + '/ac').encode('ascii') + b'\x00'))
    con = len(epflib.find_all(a9, (base + '/').encode('ascii') + b'\x00'))
    if nas != 8 or con != 1:
        raise SystemExit(f'self-check: ARM9 has {nas} NAS URLs (want 8) and {con} conntest URLs (want 1)')
    ram0, fid0, comp0, o0 = chk.overlay(0)
    if len(o0) != epflib.OVERLAY0_DECOMPRESSED_SIZE or not comp0:
        raise SystemExit('self-check: overlay 0 size/compression flag wrong')
    check_leftovers(o0, 'overlay 0')
    if len(epflib.find_all(o0, (base + '/submit_uk.php').encode('ascii') + b'\x00')) != 1:
        raise SystemExit('self-check: console URL missing from overlay 0')
    s, e = chk.fat[fid0]
    flags = struct.unpack_from('<I', chk.d, chk.ov9_off + 28)[0]
    if (flags & 0xFFFFFF) != (e - s):
        raise SystemExit(f'self-check: overlay 0 table size {flags & 0xFFFFFF:#x} != file size {e - s:#x}')
    x = chk.file(chk.file_id(epflib.DGAMER_XML))
    check_leftovers(x, 'dgamer.xml')
    if (base + '/dgamer/dynamic/config?id=153577').encode() not in x:
        raise SystemExit('self-check: dgamer.xml configURL not rewritten')
    crc = struct.unpack_from('<H', chk.d, 0x15E)[0]
    print(f'self-check OK: ARM9 {len(a9):#x} bytes (compressed {chk.arm9_size:#x}), overlay 0 {len(o0):#x} bytes '
          f'(compressed {e - s:#x}), {sum(1 for _ in chk.overlays())} overlays, header CRC16 {crc:#06x}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', default=DEFAULT_SRC, help='your clean US ROM dump (CLPE)')
    ap.add_argument('--out', default=DEFAULT_OUT, help='patched ROM to write (default: next to the dump)')
    ap.add_argument('--base', default=DEFAULT_BASE, help='http://host[/prefix] (host part at most 21 chars, plain http)')
    args = ap.parse_args()
    if not args.src:
        ap.error('--src <your dump>.nds is required')
    if not args.out:
        args.out = str(Path(args.src).with_name(Path(args.src).stem + ' (CP Chilled).nds'))

    base = args.base.rstrip('/')
    m = re.fullmatch(r'http://([A-Za-z0-9.\-]+)(/[A-Za-z0-9._\-/]*)?', base)
    if not m:
        raise SystemExit('--base must look like http://host or http://host/prefix (plain http: the DS cannot do TLS)')
    if len(m.group(1)) > 21:
        raise SystemExit('host part must be at most 21 characters to fit the in-ROM string budget')

    try:
        import ndspy.code
        import ndspy.rom
    except ImportError:
        raise SystemExit('ndspy missing: python -m pip install ndspy')

    src = Path(args.src)
    out = Path(args.out)
    print(f'source: {src}')
    rom = ndspy.rom.NintendoDSRom.fromFile(str(src))
    if rom.idCode != epflib.GAME_CODE:
        raise SystemExit(f'not the US Elite Penguin Force ROM (game code {rom.idCode!r}, expected CLPE)')
    if len(rom.arm9) != epflib.ARM9_COMPRESSED_SIZE:
        raise SystemExit(f'unexpected ARM9 size {len(rom.arm9):#x}; this patcher knows only the US rev 2 dump')

    # ---- ARM9 (Nintendo DWC library strings) ----
    arm9 = rom.loadArm9()
    if len(arm9.save(compress=False)) != epflib.ARM9_DECOMPRESSED_SIZE:
        raise SystemExit('unexpected decompressed ARM9 size')
    counts = {old: 0 for old, _, _, _ in ARM9_RULES}
    for sec in arm9.sections:
        buf = bytearray(sec.data)
        for old, path, expected, what in ARM9_RULES:
            counts[old] += replace_padded(buf, old, (base + path).encode('ascii'))
        sec.data = buf
    for old, path, expected, what in ARM9_RULES:
        if counts[old] != expected:
            raise SystemExit(f'ARM9: expected {expected} x {what}, found {counts[old]}')
        print(f'  arm9:     {what:20s} {old.decode():46s} -> {base + path}  (x{counts[old]})')
    rom.arm9 = arm9.save(compress=True)

    # ---- overlay 0 (game code: Club Penguin console URL) ----
    overlays = rom.loadArm9Overlays()
    ov0 = overlays[0]
    if len(ov0.data) != epflib.OVERLAY0_DECOMPRESSED_SIZE:
        raise SystemExit(f'unexpected overlay 0 size {len(ov0.data):#x}')
    buf = bytearray(ov0.data)
    apply_rules(buf, base, OV0_RULES, 'overlay0')
    ov0.data = buf
    rom.files[ov0.fileID] = ov0.save(compress=True)
    rom.arm9OverlayTable = ndspy.code.saveOverlayTable(overlays)

    # ---- dgamer.xml (plain text, length may change) ----
    fid = rom.filenames.idOf(epflib.DGAMER_XML)
    xml = bytes(rom.files[fid])
    if xml.count(XML_OLD) != 1:
        raise SystemExit('dgamer.xml: expected exactly one disney.go.com URL')
    rom.files[fid] = xml.replace(XML_OLD, base.encode('ascii'))
    print(f'  dgamer.xml: configURL host {XML_OLD.decode()} -> {base}')

    out.parent.mkdir(parents=True, exist_ok=True)
    rom.saveToFile(str(out))
    print(f'wrote {out} ({out.stat().st_size} bytes)')
    self_check(out, base)


if __name__ == '__main__':
    main()
