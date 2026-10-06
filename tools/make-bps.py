"""Build (or apply) a BPS patch between the clean Elite Penguin Force dump and the CP Chilled ROM.

ndspy re-lays out the whole cartridge image when the ARM9 and overlay 0 are recompressed, so a byte-for-byte diff
is ~59 MB. Both images still contain the same files (the FAT says where), so this encoder emits one SourceCopy per
unchanged file and raw data only for the header, ARM9, overlay table, overlay 0, FNT/FAT and dgamer.xml: ~1.2 MB.
The patch applies with any BPS tool (RomPatcher.js in a browser, Floating IPS, beat) to a player's own dump.

Run:  python tools/make-bps.py build <clean.nds> <patched.nds> <out.bps>
      python tools/make-bps.py apply <clean.nds> <patch.bps> <out.nds>     (reference decoder, also used to verify)
"""
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import epflib  # noqa: E402

SOURCE_READ, TARGET_READ, SOURCE_COPY, TARGET_COPY = 0, 1, 2, 3


def varint(n):
    out = bytearray()
    while True:
        x = n & 0x7f
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def read_varint(buf, pos):
    data, shift = 0, 1
    while True:
        x = buf[pos]
        pos += 1
        data += (x & 0x7f) * shift
        if x & 0x80:
            return data, pos
        shift <<= 7
        data += shift


class Writer:
    def __init__(self, src, tgt):
        self.src, self.tgt = src, tgt
        self.out = bytearray(b'BPS1' + varint(len(src)) + varint(len(tgt)) + varint(0))
        self.src_rel = 0
        self.stats = {'source_read': 0, 'target_read': 0, 'source_copy': 0, 'actions': 0}

    def action(self, kind, length):
        self.out += varint(((length - 1) << 2) | kind)
        self.stats['actions'] += 1

    def source_read(self, length):
        self.action(SOURCE_READ, length)
        self.stats['source_read'] += length

    def target_read(self, data):
        self.action(TARGET_READ, len(data))
        self.out += data
        self.stats['target_read'] += len(data)

    def source_copy(self, src_off, length):
        self.action(SOURCE_COPY, length)
        rel = src_off - self.src_rel
        self.out += varint((abs(rel) << 1) | (1 if rel < 0 else 0))
        self.src_rel = src_off + length
        self.stats['source_copy'] += length

    def raw(self, a, b):
        """[a, b) of the target: SourceRead where the bytes also sit at the same offset of the source, else TargetRead."""
        src, tgt = self.src, self.tgt
        i = a
        while i < b:
            same = i < len(src) and src[i] == tgt[i]
            j = i + 1
            if same:
                while j < b and j < len(src):
                    step = min(4096, b - j, len(src) - j)
                    if src[j:j + step] == tgt[j:j + step]:
                        j += step
                        continue
                    while j < b and j < len(src) and src[j] == tgt[j]:
                        j += 1
                    break
                self.source_read(j - i)
            else:
                while j < b and (j >= len(src) or src[j] != tgt[j]):
                    j += 1
                self.target_read(tgt[i:j])
            i = j

    def finish(self):
        self.out += struct.pack('<II', zlib.crc32(self.src) & 0xffffffff, zlib.crc32(self.tgt) & 0xffffffff)
        self.out += struct.pack('<I', zlib.crc32(self.out) & 0xffffffff)
        return bytes(self.out)


def build(clean_path, patched_path, out_path):
    src = Path(clean_path).read_bytes()
    tgt = Path(patched_path).read_bytes()
    rs, rt = epflib.Rom(src), epflib.Rom(tgt)
    assert rs.code == rt.code == epflib.GAME_CODE, 'both images must be the US Elite Penguin Force cartridge (CLPE)'
    # files that are byte-identical in both images (overlays are FAT entries too)
    moves = []
    for fid, (ts, te) in enumerate(rt.fat):
        if fid >= len(rs.fat) or te <= ts:
            continue
        ss, se = rs.fat[fid]
        if se - ss == te - ts and tgt[ts:te] == src[ss:se]:
            moves.append((ts, te, ss))
    moves.sort()
    w = Writer(src, tgt)
    pos = 0
    for ts, te, ss in moves:
        if ts < pos:        # overlapping FAT entries never happen in a sane image; keep the walk monotonic
            continue
        if ts > pos:
            w.raw(pos, ts)
        w.source_copy(ss, te - ts)
        pos = te
    if pos < len(tgt):
        w.raw(pos, len(tgt))
    patch = w.finish()
    Path(out_path).write_bytes(patch)
    s = w.stats
    print(f'{out_path}: {len(patch):,} bytes; {len(moves)} unchanged files copied ({s["source_copy"]:,} B), '
          f'{s["source_read"]:,} B same-offset, {s["target_read"]:,} B raw, {s["actions"]} actions')
    # verify with the reference decoder
    back = apply_bytes(src, patch)
    assert back == tgt, 'self-check failed: decoded image differs'
    print('self-check OK: patch re-applied to the clean dump reproduces the CP Chilled ROM')


def apply_bytes(src, patch):
    assert patch[:4] == b'BPS1', 'not a BPS patch'
    pos = 4
    src_size, pos = read_varint(patch, pos)
    tgt_size, pos = read_varint(patch, pos)
    meta_size, pos = read_varint(patch, pos)
    pos += meta_size
    assert len(src) == src_size, f'source size mismatch: patch expects {src_size:,} bytes, got {len(src):,}'
    src_crc, tgt_crc, patch_crc = struct.unpack_from('<III', patch, len(patch) - 12)
    assert zlib.crc32(patch[:-4]) & 0xffffffff == patch_crc, 'patch CRC mismatch'
    assert zlib.crc32(src) & 0xffffffff == src_crc, 'this is not the dump the patch was made for (source CRC mismatch)'
    out = bytearray(tgt_size)
    o = src_rel = tgt_rel = 0
    end = len(patch) - 12
    while pos < end:
        data, pos = read_varint(patch, pos)
        kind, length = data & 3, (data >> 2) + 1
        if kind == SOURCE_READ:
            out[o:o + length] = src[o:o + length]
        elif kind == TARGET_READ:
            out[o:o + length] = patch[pos:pos + length]
            pos += length
        elif kind == SOURCE_COPY:
            data, pos = read_varint(patch, pos)
            src_rel += (-1 if data & 1 else 1) * (data >> 1)
            out[o:o + length] = src[src_rel:src_rel + length]
            src_rel += length
        else:
            data, pos = read_varint(patch, pos)
            tgt_rel += (-1 if data & 1 else 1) * (data >> 1)
            for k in range(length):      # byte by byte: the run may overlap its own output
                out[o + k] = out[tgt_rel + k]
            tgt_rel += length
        o += length
    assert o == tgt_size and zlib.crc32(out) & 0xffffffff == tgt_crc, 'output CRC mismatch'
    return bytes(out)


def main():
    if len(sys.argv) != 5 or sys.argv[1] not in ('build', 'apply'):
        raise SystemExit(__doc__)
    if sys.argv[1] == 'build':
        build(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        out = apply_bytes(Path(sys.argv[2]).read_bytes(), Path(sys.argv[3]).read_bytes())
        Path(sys.argv[4]).write_bytes(out)
        print(f'wrote {sys.argv[4]} ({len(out):,} bytes)')


if __name__ == '__main__':
    main()
