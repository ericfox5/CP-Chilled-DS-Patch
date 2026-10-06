"""Shared helpers for the Club Penguin: Elite Penguin Force (DS, CLPE) tooling.

BLZ ("backwards LZ") decode for the ARM9/overlays, header + FNT/FAT parsing, overlay table walk.
Pure Python, no third-party dependency (the patcher uses ndspy for *writing*; this module is for inspection and self-checks).
"""
import re
import struct

GAME_CODE = b'CLPE'
ARM9_COMPRESSED_SIZE = 0x52600
ARM9_DECOMPRESSED_SIZE = 0x6d478
OVERLAY0_DECOMPRESSED_SIZE = 0x13b1a0
DGAMER_XML = 'DGamer/GameData/dgamer.xml'


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def blz_decode(pak):
    """Decode Nintendo's BLZ code compression (same footer scheme as CUE's blz.c). Returns bytes; a buffer
    whose footer says 'not compressed' (inc_len == 0) is returned unchanged."""
    pak_len = len(pak)
    inc_len = u32(pak, pak_len - 4)
    if inc_len == 0:
        return bytes(pak)
    hdr_len = pak[pak_len - 5]
    enc_len = u32(pak, pak_len - 8) & 0xFFFFFF
    dec_len = pak_len - enc_len
    plen = enc_len - hdr_len
    raw_len = dec_len + enc_len + inc_len
    raw = bytearray(pak[:dec_len])
    data = bytes(reversed(pak[dec_len:dec_len + plen]))
    out = bytearray()
    i = 0
    mask = 0
    flags = 0
    target = raw_len - dec_len
    while len(out) < target:
        mask >>= 1
        if mask == 0:
            if i >= len(data):
                break
            flags = data[i]
            i += 1
            mask = 0x80
        if not (flags & mask):
            if i >= len(data):
                break
            out.append(data[i])
            i += 1
        else:
            if i + 1 >= len(data):
                break
            pos = (data[i] << 8) | data[i + 1]
            i += 2
            ln = (pos >> 12) + 3
            if len(out) + ln > target:
                ln = target - len(out)
            pos = (pos & 0xFFF) + 3
            for _ in range(ln):
                out.append(out[len(out) - pos])
    out.reverse()
    return bytes(raw + out)


class Rom:
    """Read-only view of a .nds image: header fields, file table, overlays (decompressed on demand)."""

    def __init__(self, data):
        self.d = data
        d = data
        self.title = d[0:12].rstrip(b'\0').decode('ascii', 'replace')
        self.code = d[12:16]
        self.maker = d[16:18]
        self.arm9_off, self.arm9_entry, self.arm9_ram, self.arm9_size = u32(d, 0x20), u32(d, 0x24), u32(d, 0x28), u32(d, 0x2C)
        self.arm7_off, self.arm7_size = u32(d, 0x30), u32(d, 0x3C)
        self.fnt_off, self.fnt_size, self.fat_off, self.fat_size = u32(d, 0x40), u32(d, 0x44), u32(d, 0x48), u32(d, 0x4C)
        self.ov9_off, self.ov9_size = u32(d, 0x50), u32(d, 0x54)
        self.fat = [(u32(d, self.fat_off + i * 8), u32(d, self.fat_off + i * 8 + 4)) for i in range(self.fat_size // 8)]
        self.names = self._parse_fnt()

    @classmethod
    def from_file(cls, path):
        with open(path, 'rb') as f:
            return cls(f.read())

    def _parse_fnt(self):
        d, fnt = self.d, self.fnt_off
        names = {}

        def walk(dir_id, path):
            e = fnt + (dir_id & 0xFFF) * 8
            p = fnt + u32(d, e)
            fid = u16(d, e + 4)
            while True:
                l = d[p]
                p += 1
                if l == 0:
                    break
                ln = l & 0x7F
                nm = d[p:p + ln].decode('ascii', 'replace')
                p += ln
                if l & 0x80:
                    sid = u16(d, p)
                    p += 2
                    walk(sid, path + nm + '/')
                else:
                    names[fid] = path + nm
                    fid += 1
        walk(0xF000, '')
        return names

    def file(self, fid):
        s, e = self.fat[fid]
        return self.d[s:e]

    def file_id(self, path):
        for fid, nm in self.names.items():
            if nm == path:
                return fid
        raise KeyError(path)

    def arm9_compressed(self):
        return self.d[self.arm9_off:self.arm9_off + self.arm9_size]

    def arm9(self):
        return blz_decode(self.arm9_compressed())

    def overlays(self):
        """Yields (overlay_id, ram_addr, file_id, compressed_flag, decompressed_bytes)."""
        for k in range(self.ov9_size // 32):
            e = self.ov9_off + k * 32
            oid, ram, size, bss, sinit, einit, fid, flags = struct.unpack_from('<8I', self.d, e)
            comp = bool(flags & 0x01000000)
            blob = self.file(fid)
            yield oid, ram, fid, comp, (blz_decode(blob) if comp else bytes(blob))

    def overlay(self, oid):
        for o, ram, fid, comp, data in self.overlays():
            if o == oid:
                return ram, fid, comp, data
        raise KeyError(oid)


def strings(blob, minlen=4):
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % minlen, blob):
        yield m.start(), m.group()


def find_all(blob, needle):
    return [m.start() for m in re.finditer(re.escape(needle), blob)]
