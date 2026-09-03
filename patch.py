#!/usr/bin/env python3
"""Bigger, sharp subtitles for Batman: Arkham Asylum GOTY (Steam).

The game draws dialogue subtitles natively with the bitmap font
BmFonts.MediumFont (the same font used for "Loading" and the cutscene
"press X to skip" prompt). Its glyphs are 15 px tall and the size is never
adapted to the screen resolution, so on 1440p / 4K / ultrawide displays the
subtitles are tiny. The SubtitleFontName ini setting is ignored by the game.

This tool re-renders that font at a larger size from the vector Rockwell
font that ships inside the game itself (the Scaleform font library), and
writes it into the two packages that carry a copy of the bitmap font. It
patches YOUR OWN game files - no game content is distributed. Every structure
is parsed and verified before writing; on an unexpected file layout it aborts
without touching anything, and it keeps a ".original" backup of each file it
replaces.

Double-click the .exe (or run "python patch.py" with no arguments) for an
interactive menu. Command line:

  python patch.py --apply                  # patch at 2x, auto-detect Steam
  python patch.py --apply --scale 1.5      # any scale between 1.2 and 3
  python patch.py --apply --game-dir "D:\\SteamLibrary\\steamapps\\common\\Batman Arkham Asylum GOTY"
  python patch.py --apply --font Rockwell.ttf   # render from a TrueType file instead
  python patch.py --restore                # put the original files back

Requires Windows, Python 3.8+ and Pillow (the .exe release bundles both).
"""

import argparse
import io
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required:  pip install -r requirements.txt")

DECOMPRESS_URL = "https://www.gildor.org/down/47/umodel/decompress.zip"
FONT_NAME = "BmFonts.MediumFont"
FONTLIB_EXPORT = "fonts_en.fonts_en"
FONTLIB_FACE = "Rockwell WGL"
GAME_FOLDER = "Batman Arkham Asylum GOTY"
PKG_STORE_COMPRESSED = 0x02000000


# ----------------------------------------------------------------------------
# Unreal Engine 3 package (v576/21) tables
# ----------------------------------------------------------------------------
class Pkg:
    def __init__(self, path):
        self.path = path
        self.data = d = bytearray(open(path, "rb").read())
        if d[:4] != b"\xc1\x83\x2a\x9e":
            raise ValueError("not an Unreal package: " + path)
        p = 12
        fl = self.i32(p)
        p += 4 + (fl if fl > 0 else -fl * 2)
        self.pkg_flags = self.u32(p)
        p += 4
        self.name_count, self.name_off = self.u32(p), self.u32(p + 4)
        self.exp_count, self.exp_off = self.u32(p + 8), self.u32(p + 12)
        self.imp_count, self.imp_off = self.u32(p + 16), self.u32(p + 20)
        self.names = []
        q = self.name_off
        for _ in range(self.name_count):
            ln = self.i32(q)
            q += 4
            if ln > 0:
                self.names.append(d[q:q + ln - 1].decode("latin-1"))
                q += ln + 8
            else:
                self.names.append(d[q:q + (-ln) * 2 - 2].decode("utf-16-le"))
                q += (-ln) * 2 + 8
        self.imports = []
        q = self.imp_off
        for _ in range(self.imp_count):
            self.imports.append(dict(cls=self.fname(q + 8), outer=self.i32(q + 16), name=self.fname(q + 20)))
            q += 28
        self.exports = []
        q = self.exp_off
        for i in range(self.exp_count):
            e = dict(idx=i + 1, cls=self.i32(q), outer=self.i32(q + 8), name=self.fname(q + 12),
                     size=self.i32(q + 32), off=self.i32(q + 36), entry=q)
            q += 44
            ngen = self.i32(q)
            q += 4 + ngen * 4 + 16 + 4
            self.exports.append(e)

    def u32(self, o): return struct.unpack_from("<I", self.data, o)[0]
    def i32(self, o): return struct.unpack_from("<i", self.data, o)[0]

    def fname(self, o):
        idx, num = self.u32(o), self.u32(o + 4)
        n = self.names[idx] if idx < len(self.names) else "?"
        return "%s_%d" % (n, num - 1) if num else n

    def objname(self, ref):
        if ref > 0: return self.exports[ref - 1]["name"]
        if ref < 0: return self.imports[-ref - 1]["name"]
        return "None"

    def path_of(self, ref):
        parts = []
        while ref:
            if ref > 0:
                e = self.exports[ref - 1]; parts.append(e["name"]); ref = e["outer"]
            else:
                i = self.imports[-ref - 1]; parts.append(i["name"]); ref = i["outer"]
        return ".".join(reversed(parts))

    def export(self, path):
        for e in self.exports:
            if self.path_of(e["idx"]) == path:
                return e
        raise ValueError("export not found: " + path)

    def name_at(self, o):
        return self.names[self.u32(o)]

    def parse_props(self, off):
        """Property list starting at off -> ({name: value offset}, offset after 'None')."""
        o, out = off, {}
        while True:
            n = self.name_at(o); o += 8
            if n == "None":
                return out, o
            t = self.name_at(o); o += 8
            sz = self.i32(o); o += 8
            if t == "BoolProperty":
                out[n] = o; o += 4; continue
            if t == "StructProperty":
                o += 8
            out[n] = o; o += sz


def is_compressed(path):
    d = open(path, "rb").read(64)
    fl = struct.unpack_from("<i", d, 12)[0]
    p = 16 + (fl if fl > 0 else -fl * 2)
    return bool(struct.unpack_from("<I", d, p)[0] & PKG_STORE_COMPRESSED)


# ----------------------------------------------------------------------------
# DXT5
# ----------------------------------------------------------------------------
def decode_dxt5(buf, w, h):
    img = bytearray(w * h * 4); bi = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            a0, a1 = buf[bi], buf[bi + 1]
            abits = int.from_bytes(buf[bi + 2:bi + 8], "little")
            c0, c1 = struct.unpack_from("<HH", buf, bi + 8)
            cbits = struct.unpack_from("<I", buf, bi + 12)[0]
            bi += 16
            pal = []
            for c in (c0, c1):
                pal.append((((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31))
            if c0 > c1:
                pal.append(tuple((2 * a + b) // 3 for a, b in zip(pal[0], pal[1])))
                pal.append(tuple((a + 2 * b) // 3 for a, b in zip(pal[0], pal[1])))
            else:
                pal.append(tuple((a + b) // 2 for a, b in zip(pal[0], pal[1]))); pal.append((0, 0, 0))
            apal = [a0, a1]
            if a0 > a1: apal += [((7 - j) * a0 + j * a1) // 7 for j in range(1, 7)]
            else: apal += [((5 - j) * a0 + j * a1) // 5 for j in range(1, 5)] + [0, 255]
            for py in range(4):
                for px in range(4):
                    i = py * 4 + px; o = ((by + py) * w + bx + px) * 4
                    r, g, b = pal[(cbits >> (2 * i)) & 3]
                    img[o:o + 4] = bytes((r, g, b, apal[(abits >> (3 * i)) & 7]))
    return img


def encode_dxt5(px, w, h):
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            alphas = []
            for py in range(4):
                for pxx in range(4):
                    alphas.append(px[((by + py) * w + bx + pxx) * 4 + 3])
            a0, a1 = max(alphas), min(alphas)
            if a0 == a1:
                aidx = [0] * 16
            else:
                apal = [a0, a1] + [((7 - j) * a0 + j * a1) // 7 for j in range(1, 7)]
                aidx = [min(range(8), key=lambda i, a=a: abs(apal[i] - a)) for a in alphas]
            abits = 0
            for i, v in enumerate(aidx):
                abits |= v << (3 * i)
            out += bytes((a0, a1)) + abits.to_bytes(6, "little") + struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)
    return bytes(out)


# ----------------------------------------------------------------------------
# SWF / GFX DefineFont3 parser + glyph rasterizer
# ----------------------------------------------------------------------------
class Bits:
    def __init__(self, data, pos=0):
        self.data, self.pos, self.bit = data, pos, 0

    def ub(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.data[self.pos] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0; self.pos += 1
        return v

    def sb(self, n):
        v = self.ub(n)
        return v - (1 << n) if n and v & (1 << (n - 1)) else v


def parse_glyph_shape(b):
    nfill = b.ub(4); nline = b.ub(4)
    x = y = 0; contours = []; cur = []
    while True:
        if b.ub(1) == 0:
            flags = b.ub(5)
            if flags == 0:
                break
            if flags & 1:
                nb = b.ub(5); x = b.sb(nb); y = b.sb(nb)
                if cur: contours.append(cur)
                cur = [(x, y)]
            if flags & 2: b.ub(nfill)
            if flags & 4: b.ub(nfill)
            if flags & 8: b.ub(nline)
            if flags & 16:
                raise ValueError("unexpected style record in glyph")
        else:
            if b.ub(1):
                nb = b.ub(4) + 2
                if b.ub(1):
                    x += b.sb(nb); y += b.sb(nb)
                elif b.ub(1):
                    y += b.sb(nb)
                else:
                    x += b.sb(nb)
                cur.append((x, y))
            else:
                nb = b.ub(4) + 2
                cx = x + b.sb(nb); cy = y + b.sb(nb)
                ax = cx + b.sb(nb); ay = cy + b.sb(nb)
                x0, y0 = x, y
                for i in range(1, 9):
                    t = i / 8
                    cur.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * ax,
                                (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * ay))
                x, y = ax, ay
    if cur: contours.append(cur)
    return contours


def parse_swf_fonts(swf):
    if swf[:3] not in (b"GFX", b"FWS"):
        raise ValueError("not an uncompressed SWF/GFX movie")
    p = 8
    nbits = swf[p] >> 3; p += (5 + nbits * 4 + 7) // 8 + 4
    fonts = {}
    while p < len(swf):
        cl = struct.unpack_from("<H", swf, p)[0]; code, ln = cl >> 6, cl & 0x3F; p += 2
        if ln == 0x3F: ln = struct.unpack_from("<I", swf, p)[0]; p += 4
        body = swf[p:p + ln]; p += ln
        if code == 0: break
        if code != 75: continue
        o = 2
        flags = body[o]; o += 2
        nlen = body[o]; o += 1
        name = body[o:o + nlen].split(b"\0")[0].decode("latin-1"); o += nlen
        n = struct.unpack_from("<H", body, o)[0]; o += 2
        fmt, sz = ("<I", 4) if flags & 0x08 else ("<H", 2)
        table = o
        offs = [struct.unpack_from(fmt, body, table + i * sz)[0] for i in range(n)]
        code_off = struct.unpack_from(fmt, body, table + n * sz)[0]
        shapes = [parse_glyph_shape(Bits(body, table + offs[i])) for i in range(n)]
        o = table + code_off
        codes = [struct.unpack_from("<H", body, o + 2 * i)[0] for i in range(n)]; o += 2 * n
        if not flags & 0x80:
            continue
        asc, desc, lead = struct.unpack_from("<hhh", body, o); o += 6
        adv = [struct.unpack_from("<h", body, o + 2 * i)[0] for i in range(n)]
        fonts[name] = dict(codes={c: i for i, c in enumerate(codes)}, shapes=shapes, ascent=asc, advance=adv)
    return fonts


def rasterize_contours(contours, scale, ss=4):
    xs = [x for c in contours for x, y in c]; ys = [y for c in contours for x, y in c]
    if not xs:
        return None, 0, 0
    x0 = math.floor(min(xs) * scale) - 1; y0 = math.floor(min(ys) * scale) - 1
    x1 = math.ceil(max(xs) * scale) + 1; y1 = math.ceil(max(ys) * scale) + 1
    W, H = (x1 - x0) * ss, (y1 - y0) * ss
    edges = []
    for c in contours:
        pts = [((x * scale - x0) * ss, (y * scale - y0) * ss) for x, y in c]
        if pts[0] != pts[-1]: pts.append(pts[0])
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if ay != by: edges.append((ax, ay, bx, by))
    buf = bytearray(W * H)
    for row in range(H):
        sy = row + 0.5
        cross = []
        for ax, ay, bx, by in edges:
            if (ay <= sy < by) or (by <= sy < ay):
                cross.append((ax + (sy - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1))
        if not cross: continue
        cross.sort(); wind = 0; base = row * W
        for i in range(len(cross) - 1):
            wind += cross[i][1]
            if wind != 0:
                a = max(0, int(round(cross[i][0]))); b = min(W, int(round(cross[i + 1][0])))
                if b > a: buf[base + a:base + b] = b"\xff" * (b - a)
    img = Image.frombytes("L", (W, H), bytes(buf)).resize((W // ss, H // ss), Image.BOX)
    return img, x0, y0


class SwfGlyphs:
    """Glyph source: a DefineFont3 face from the game's own font library."""
    def __init__(self, swf, face):
        fonts = parse_swf_fonts(swf)
        if face not in fonts:
            raise ValueError("font %r not in font library (found: %s)" % (face, ", ".join(fonts)))
        self.f = fonts[face]
        self.label = face + " (from the game's font library)"

    def has(self, ch): return ord(ch) in self.f["codes"]

    def cap_height(self, scale):
        c = self.f["shapes"][self.f["codes"][ord("A")]]
        ys = [y for cc in c for x, y in cc]
        return (max(ys) - min(ys)) * scale

    def calibrate(self, target_cap_px):
        return target_cap_px / self.cap_height(1.0)

    def render(self, ch, scale):
        """-> (L image or None, left, top relative to (pen x, baseline), advance px)"""
        gi = self.f["codes"][ord(ch)]
        img, x0, y0 = rasterize_contours(self.f["shapes"][gi], scale)
        return img, x0, y0, self.f["advance"][gi] * scale


class TtfGlyphs:
    """Glyph source: a TrueType file (Pillow)."""
    def __init__(self, path, target_cap_px):
        best = None
        for size in range(8, 400):
            f = ImageFont.truetype(path, size); b = f.getbbox("A"); h = b[3] - b[1]
            if best is None or abs(h - target_cap_px) < abs(best[1] - target_cap_px): best = (size, h)
            if h > target_cap_px + 3: break
        self.font = ImageFont.truetype(path, best[0])
        self.asc = self.font.getmetrics()[0]
        self.label = "%s at %d px" % (os.path.basename(path), best[0])

    def has(self, ch):
        b = self.font.getbbox(ch)
        return b is not None and b[2] - b[0] > 0 and b != self.font.getbbox("\ufffd")

    def calibrate(self, target_cap_px): return 1.0

    def render(self, ch, scale):
        b = self.font.getbbox(ch); adv = self.font.getlength(ch)
        if b is None or b[2] - b[0] == 0:
            return None, 0, 0, adv
        l, t, r, btm = b
        canvas = Image.new("L", (r + 2, btm + 2), 0)
        ImageDraw.Draw(canvas).text((0, 0), ch, font=self.font, fill=255)
        return canvas.crop((l, t, r, btm)), l, t - self.asc, adv


# ----------------------------------------------------------------------------
# Font rebuild
# ----------------------------------------------------------------------------
def rebuild_font(pkg, font_path, glyphs, scale, log):
    d = pkg.data
    fe = pkg.export(font_path)
    if pkg.objname(fe["cls"]) != "Font":
        raise ValueError("%s is not a Font export" % font_path)
    props, pend = pkg.parse_props(fe["off"] + 4)
    carr = props["Characters"]
    cnt = pkg.i32(carr)
    chars = []
    for k in range(cnt):
        e = carr + 4 + k * 21
        su, sv, us, vs = struct.unpack_from("<4i", d, e)
        chars.append(dict(off=e, su=su, sv=sv, us=us, vs=vs, page=d[e + 16], vo=pkg.i32(e + 17)))
    if cnt < 128 or chars[65]["vs"] == 0:
        raise ValueError("unexpected character table in " + font_path)
    rcnt = pkg.i32(pend)
    remap = {}
    for i in range(rcnt):
        u, idx = struct.unpack_from("<HH", d, pend + 4 + 4 * i)
        remap[idx] = u
    texarr = props["Textures"]
    texrefs = [pkg.i32(texarr + 4 + 4 * i) for i in range(pkg.i32(texarr))]
    line_h = max(c["vo"] + c["vs"] for c in chars)
    capA = chars[65]["vs"]
    baseline = chars[65]["vo"] + capA
    log("  %s: %d glyphs, line %d px, cap height %d px, %d texture page(s)" % (font_path, cnt, line_h, capA, len(texrefs)))

    pages_img = []
    for ref in texrefs:
        te = pkg.exports[ref - 1]
        tp, tend = pkg.parse_props(te["off"] + 4)
        if pkg.name_at(tp["Format"]) != "PF_DXT5":
            raise ValueError("unexpected texture format")
        sx, sy = pkg.i32(tp["SizeX"]), pkg.i32(tp["SizeY"])
        q = tend + 16 + 4 + 16
        pages_img.append(Image.frombytes("RGBA", (sx, sy), bytes(decode_dxt5(bytes(d[q:q + sx * sy]), sx, sy))))

    L = round(line_h * scale)
    BASE = round(baseline * scale)
    gscale = glyphs.calibrate(round(capA * scale))
    log("  rendering %s, cap height %d px, line %d px" % (glyphs.label, round(capA * scale), L))

    tiles = []
    for i, c in enumerate(chars):
        u = remap.get(i, i)
        if c["us"] == 0 and c["vs"] == 0:
            tiles.append((i, None, 0, 0, 0)); continue
        ch = chr(u)
        if u >= 0x4700 or u in (0xA0D, 0xD00) or not glyphs.has(ch):   # icons / unknown: keep, upscale
            img = pages_img[c["page"]].crop((c["su"], c["sv"], c["su"] + c["us"], c["sv"] + c["vs"])).getchannel("A")
            w, h = max(1, round(c["us"] * scale)), max(1, round(c["vs"] * scale))
            tiles.append((i, img.resize((w, h), Image.LANCZOS), w, h, round(c["vo"] * scale))); continue
        img, gl, gt, adv = glyphs.render(ch, gscale)
        if img is None or ch == " ":
            w = max(1, round(adv)); tiles.append((i, Image.new("L", (w, L), 0), w, L, 0)); continue
        w = max(round(adv), gl + img.width + 1)
        top = BASE + gt                       # glyph top in line coordinates
        vo = max(0, top)
        tile = Image.new("L", (w, img.height - (vo - top)), 0)
        tile.paste(img, (max(0, gl), top - vo))
        tiles.append((i, tile, w, tile.height, vo))

    def rgba(tile):
        return Image.merge("RGBA", (Image.new("L", tile.size, 255),) * 3 + (tile,))

    PW = 1024
    pages, items = [], []
    cur = Image.new("RGBA", (PW, 1024), (255, 255, 255, 0)); x = y = rowh = 0
    for i, tile, w, h, vo in tiles:
        if tile is None: continue
        if x + w + 1 > PW:
            x = 0; y += rowh + 1; rowh = 0
        if y + h + 1 > 1024:
            pages.append((cur, items)); cur = Image.new("RGBA", (PW, 1024), (255, 255, 255, 0)); items = []; x = y = rowh = 0
        cur.paste(rgba(tile), (x, y)); items.append((i, x, y)); x += w + 1; rowh = max(rowh, h)
    pages.append((cur, items))
    if len(pages) > len(texrefs):
        raise ValueError("scale too large: glyphs need %d pages, font has %d" % (len(pages), len(texrefs)))
    final = []
    for img, its in pages:
        used = max(y + tiles[i][3] for i, x, y in its) + 1
        H = 1 << math.ceil(math.log2(used))
        final.append((img.crop((0, 0, PW, H)), its))

    pos = {i: (pi, x, y) for pi, (im, its) in enumerate(final) for i, x, y in its}
    for i, tile, w, h, vo in tiles:
        c = chars[i]
        pg, su, sv = pos[i] if tile is not None else (0, 0, 0)
        struct.pack_into("<4i", d, c["off"], su, sv, w, h); d[c["off"] + 16] = pg; struct.pack_into("<i", d, c["off"] + 17, vo)

    for pi, ref in enumerate(texrefs):
        te = pkg.exports[ref - 1]
        tp, tend = pkg.parse_props(te["off"] + 4)
        img = final[pi][0] if pi < len(final) else Image.new("RGBA", (4, 4), (255, 255, 255, 0))
        W, H = img.size
        newmip = encode_dxt5(img.tobytes(), W, H)
        q = tend + 16 + 4 + 16
        sx0, sy0 = pkg.i32(tp["SizeX"]), pkg.i32(tp["SizeY"])
        guid = bytes(d[q + sx0 * sy0 + 8:q + sx0 * sy0 + 24])
        blob = bytearray(d[te["off"]:tend])
        rel = lambda a: a - te["off"]
        struct.pack_into("<i", blob, rel(tp["SizeX"]), W)
        struct.pack_into("<i", blob, rel(tp["SizeY"]), H)
        struct.pack_into("<i", blob, rel(tp["MipTailBaseIdx"]), int(math.log2(max(W, H))))
        new_off = len(d)
        tail = struct.pack("<4I", 0, 0, 0, new_off + len(blob) + 16) + struct.pack("<I", 1)
        mip_pos = new_off + len(blob) + len(tail) + 16
        tail += struct.pack("<4I", 0, len(newmip), len(newmip), mip_pos) + newmip + struct.pack("<2I", W, H) + guid
        blob += tail
        d += blob
        struct.pack_into("<i", d, te["entry"] + 32, len(blob))
        struct.pack_into("<i", d, te["entry"] + 36, new_off)
        log("  texture page %d: %dx%d" % (pi, W, H))


def extract_fontlib(pkg):
    e = pkg.export(FONTLIB_EXPORT)
    blob = pkg.data[e["off"]:e["off"] + e["size"]]
    g = blob.find(b"GFX")
    if g < 0 or g > 4096:
        raise ValueError("font library movie not found in " + pkg.path)
    flen = struct.unpack_from("<I", blob, g + 4)[0]
    return bytes(blob[g:g + flen])


# ----------------------------------------------------------------------------
# decompress.exe (Gildor), Steam detection
# ----------------------------------------------------------------------------
def base_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def get_decompressor():
    for cand in (os.path.join(base_dir(), "tools", "decompress.exe"),
                 os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tools", "decompress.exe")):
        if os.path.exists(cand):
            return cand
    tools = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "tools")
    os.makedirs(tools, exist_ok=True)
    exe = os.path.join(tools, "decompress.exe")
    print("Downloading Gildor's decompress tool from %s ..." % DECOMPRESS_URL)
    data = urllib.request.urlopen(DECOMPRESS_URL, timeout=60).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.lower().endswith("decompress.exe"):
                open(exe, "wb").write(z.read(n)); break
    if not os.path.exists(exe):
        raise RuntimeError("could not obtain decompress.exe - download it from gildor.org into " + tools)
    return exe


def decompress(exe, src):
    tmp = tempfile.mkdtemp(prefix="aa-subs-")
    subprocess.run([exe, "-game=batman", "-out=" + tmp, src], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for r, _, fs in os.walk(tmp):
        for f in fs:
            if f.lower() == os.path.basename(src).lower():
                return os.path.join(r, f), tmp
    raise RuntimeError("decompression failed for " + src)


def find_game_dir():
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(os.path.join(os.environ[env], "Steam"))
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    v = winreg.QueryValueEx(k, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath")[0]
                    roots.append(v)
            except OSError:
                pass
    except ImportError:
        pass
    libs = []
    for r in roots:
        libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8", errors="replace").read()):
                libs.append(m.group(1).replace("\\\\", "\\"))
    for lib in libs:
        cand = os.path.join(lib, "steamapps", "common", GAME_FOLDER)
        if os.path.isdir(os.path.join(cand, "BmGame", "CookedPC")):
            return cand
    return None


# ----------------------------------------------------------------------------
def targets_for(game_dir, lang):
    cooked = os.path.join(game_dir, "BmGame", "CookedPC")
    if not os.path.isdir(cooked):
        raise RuntimeError("game not found at %s" % game_dir)
    t = [os.path.join(cooked, "Startup_%s.upk" % lang), os.path.join(cooked, "CommonGame_LOC_%s.upk" % lang)]
    for f in t:
        if not os.path.exists(f):
            raise RuntimeError("missing package: " + f)
    return t


def do_restore(game_dir, lang, log=print):
    for t in targets_for(game_dir, lang):
        bak = t + ".original"
        if os.path.exists(bak):
            shutil.copyfile(bak, t); log("restored " + os.path.basename(t))
        else:
            log("no backup for %s - nothing to restore" % os.path.basename(t))


def do_apply(game_dir, lang, scale, ttf=None, log=print):
    if not 1.2 <= scale <= 3.0:
        raise RuntimeError("scale must be between 1.2 and 3")
    targets = targets_for(game_dir, lang)
    exe = get_decompressor()
    glyphs = None
    for t in targets:
        name = os.path.basename(t)
        bak = t + ".original"
        if not os.path.exists(bak):
            if not is_compressed(t):
                raise RuntimeError("%s is not a pristine package and no .original backup exists - "
                                   "use 'Verify integrity of game files' in Steam, then run again" % name)
            shutil.copyfile(t, bak); log("backup: " + os.path.basename(bak))
        log("patching " + name)
        dec, tmp = decompress(exe, bak)
        pkg = Pkg(dec)
        if glyphs is None:
            if ttf:
                fe = pkg.export(FONT_NAME); props, _ = pkg.parse_props(fe["off"] + 4)
                cap = struct.unpack_from("<i", pkg.data, props["Characters"] + 4 + 65 * 21 + 12)[0]
                glyphs = TtfGlyphs(ttf, round(cap * scale))
            else:
                glyphs = SwfGlyphs(extract_fontlib(pkg), FONTLIB_FACE)
        rebuild_font(pkg, FONT_NAME, glyphs, scale, log)
        open(t, "wb").write(pkg.data)
        shutil.rmtree(tmp, ignore_errors=True)
        log("  written: %s (%d bytes)" % (name, len(pkg.data)))
    log("done.")


def interactive():
    print("=" * 64)
    print("  Batman: Arkham Asylum GOTY - subtitle size fix")
    print("  Legendas maiores para o Batman: Arkham Asylum GOTY (Steam)")
    print("=" * 64)
    game = find_game_dir()
    if game:
        print("\nGame found / Jogo encontrado:\n  " + game)
    else:
        print("\nGame not found automatically. / Jogo nao encontrado automaticamente.")
        game = input("Paste the game folder path / Cole o caminho da pasta do jogo:\n> ").strip().strip('"')
    try:
        targets_for(game, "INT")
    except RuntimeError as ex:
        print("ERROR:", ex); input("\nPress Enter to exit / Enter para sair"); return
    print("""
  1) Apply 2x  (recommended)   / Aplicar 2x (recomendado)
  2) Apply 1.5x                / Aplicar 1.5x
  3) Apply 2.5x                / Aplicar 2.5x
  4) Restore original files    / Restaurar os arquivos originais
  0) Exit                      / Sair
""")
    choice = input("> ").strip()
    scales = {"1": 2.0, "2": 1.5, "3": 2.5}
    try:
        if choice in scales:
            print("\nClose the game if it is running. / Feche o jogo se estiver aberto.\n")
            do_apply(game, "INT", scales[choice])
            print("\nOK! Start the game. / Pronto! Pode abrir o jogo.")
        elif choice == "4":
            do_restore(game, "INT")
            print("\nOriginal files restored. / Arquivos originais restaurados.")
        else:
            return
    except Exception as ex:
        print("\nERROR / ERRO:", ex)
    input("\nPress Enter to exit / Enter para sair")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="patch the game (non-interactive)")
    ap.add_argument("--restore", action="store_true", help="restore the .original backups")
    ap.add_argument("--game-dir", default=None, help="game folder (default: auto-detect Steam)")
    ap.add_argument("--lang", default="INT", help="package language suffix (INT, DEU, ESN, FRA, ITA); the PT-BR fan translation uses INT")
    ap.add_argument("--scale", type=float, default=2.0, help="font size multiplier, 1.2 to 3 (default 2)")
    ap.add_argument("--font", default=None, help="render from this TrueType file instead of the game's own Rockwell")
    args = ap.parse_args()
    if not args.apply and not args.restore:
        interactive(); return
    game = args.game_dir or find_game_dir()
    if not game:
        sys.exit("game not found - use --game-dir")
    try:
        if args.restore:
            do_restore(game, args.lang)
        else:
            do_apply(game, args.lang, args.scale, args.font)
    except Exception as ex:
        sys.exit("ERROR: %s" % ex)


if __name__ == "__main__":
    main()
