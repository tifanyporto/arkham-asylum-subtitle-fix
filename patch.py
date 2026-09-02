#!/usr/bin/env python3
"""Bigger, sharp subtitles for Batman: Arkham Asylum GOTY (Steam).

The game draws dialogue subtitles natively with the bitmap font
BmFonts.MediumFont (the same font used for "Loading" and the cutscene
"press X to skip" prompt). Its glyphs are 15 px tall and the size is never
adapted to the screen resolution, so on 1440p / 4K / ultrawide displays the
subtitles are tiny. The SubtitleFontName ini setting is ignored by the game.

This tool re-renders that font at a larger size from a Rockwell TrueType
font (the typeface the game uses) and writes it into the two packages that
carry a copy of it. It patches YOUR OWN game files - no game content is
distributed. Every structure is parsed and verified before writing; on an
unexpected file layout it aborts without touching anything, and it keeps a
".original" backup of each file it replaces.

Usage:
  python patch.py                          # patch, default Steam location, 2x
  python patch.py --scale 1.5              # any scale between 1.2 and 3
  python patch.py --game-dir "D:\\Steam\\steamapps\\common\\Batman Arkham Asylum GOTY"
  python patch.py --font "C:\\path\\to\\Rockwell.ttf"
  python patch.py --dump-fontlib           # extract fonts_en.gfx (see README)
  python patch.py --restore                # put the original files back

Requires: Windows, Python 3.8+, Pillow, and a Rockwell TrueType font
(auto-detected at C:\\Windows\\Fonts\\ROCK.TTF when Microsoft Office is installed).
"""

import argparse
import io
import math
import os
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

DEFAULT_GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Batman Arkham Asylum GOTY"
DECOMPRESS_URL = "https://www.gildor.org/down/47/umodel/decompress.zip"
FONT_NAME = "BmFonts.MediumFont"
DEFAULT_TTF = r"C:\Windows\Fonts\ROCK.TTF"
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
# Font rebuild
# ----------------------------------------------------------------------------
def rebuild_font(pkg, font_path, ttf, fallback_ttf, scale, log):
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

    # original pages (icon glyphs are kept, upscaled)
    pages_img = []
    for ref in texrefs:
        te = pkg.exports[ref - 1]
        tp, tend = pkg.parse_props(te["off"] + 4)
        if pkg.name_at(tp["Format"]) != "PF_DXT5":
            raise ValueError("unexpected texture format")
        sx, sy = pkg.i32(tp["SizeX"]), pkg.i32(tp["SizeY"])
        q = tend + 16 + 4 + 16
        pages_img.append(Image.frombytes("RGBA", (sx, sy), bytes(decode_dxt5(bytes(d[q:q + sx * sy]), sx, sy))))

    target_cap = round(capA * scale)
    best = None
    for size in range(8, 300):
        f = ImageFont.truetype(ttf, size)
        b = f.getbbox("A"); h = b[3] - b[1]
        if best is None or abs(h - target_cap) < abs(best[1] - target_cap):
            best = (size, h)
        if h > target_cap + 3:
            break
    SIZE = best[0]
    font = ImageFont.truetype(ttf, SIZE)
    fb = ImageFont.truetype(fallback_ttf, SIZE) if fallback_ttf else None
    asc, _ = font.getmetrics()
    L = round(line_h * scale)
    BASE = round(baseline * scale)
    log("  rendering with %s at %d px (cap height %d px), line %d px" % (os.path.basename(ttf), SIZE, best[1], L))

    def has_glyph(f, ch):
        b = f.getbbox(ch)
        return b is not None and b[2] - b[0] > 0 and b != f.getbbox("\ufffd")

    tiles = []
    for i, c in enumerate(chars):
        u = remap.get(i, i)
        if c["us"] == 0 and c["vs"] == 0:
            tiles.append((i, None, 0, 0, 0)); continue
        if u >= 0x4700 or u in (0xA0D, 0xD00):   # icon glyphs: keep, upscale
            img = pages_img[c["page"]].crop((c["su"], c["sv"], c["su"] + c["us"], c["sv"] + c["vs"])).getchannel("A")
            w, h = max(1, round(c["us"] * scale)), max(1, round(c["vs"] * scale))
            tiles.append((i, img.resize((w, h), Image.LANCZOS), w, h, round(c["vo"] * scale))); continue
        ch = chr(u)
        f = font if has_glyph(font, ch) else (fb if fb and has_glyph(fb, ch) else font)
        adv = f.getlength(ch); b = f.getbbox(ch)
        if b is None or b[2] - b[0] == 0 or ch == " ":
            w = max(1, round(adv)); tiles.append((i, Image.new("L", (w, L), 0), w, L, 0)); continue
        l, t, r, btm = b
        w = max(round(adv), r + 1)
        top = BASE - asc + t
        vo = max(0, top)
        vs = btm - t - (vo - top)
        canvas = Image.new("L", (w + 4, L + 40), 0)
        ImageDraw.Draw(canvas).text((0, BASE - asc + 20), ch, font=f, fill=255)
        tiles.append((i, canvas.crop((0, 20 + vo, w, 20 + vo + vs)), w, vs, vo))

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


# ----------------------------------------------------------------------------
# decompress.exe (Gildor)
# ----------------------------------------------------------------------------
def get_decompressor(tools_dir):
    exe = os.path.join(tools_dir, "decompress.exe")
    if os.path.exists(exe):
        return exe
    os.makedirs(tools_dir, exist_ok=True)
    print("Downloading Gildor's decompress tool from %s ..." % DECOMPRESS_URL)
    data = urllib.request.urlopen(DECOMPRESS_URL, timeout=60).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.lower().endswith("decompress.exe"):
                open(exe, "wb").write(z.read(n))
                break
    if not os.path.exists(exe):
        sys.exit("could not obtain decompress.exe - download it from gildor.org and put it in " + tools_dir)
    return exe


def decompress(exe, src):
    tmp = tempfile.mkdtemp(prefix="aa-subs-")
    subprocess.run([exe, "-game=batman", "-out=" + tmp, src], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for r, _, fs in os.walk(tmp):
        for f in fs:
            if f.lower() == os.path.basename(src).lower():
                return os.path.join(r, f), tmp
    raise RuntimeError("decompression failed for " + src)


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR)
    ap.add_argument("--lang", default="INT", help="package language suffix (INT, DEU, ESN, FRA, ITA); PT-BR fan translations use INT")
    ap.add_argument("--scale", type=float, default=2.0, help="font size multiplier (default 2)")
    ap.add_argument("--font", default=None, help="Rockwell .ttf to render from (default: %s)" % DEFAULT_TTF)
    ap.add_argument("--dump-fontlib", action="store_true", help="write fonts_en.gfx next to this script and exit")
    ap.add_argument("--restore", action="store_true", help="restore the .original backups")
    args = ap.parse_args()

    if not 1.2 <= args.scale <= 3.0:
        sys.exit("--scale must be between 1.2 and 3")
    cooked = os.path.join(args.game_dir, "BmGame", "CookedPC")
    if not os.path.isdir(cooked):
        sys.exit("game not found at %s (use --game-dir)" % args.game_dir)
    targets = [os.path.join(cooked, "Startup_%s.upk" % args.lang),
               os.path.join(cooked, "CommonGame_LOC_%s.upk" % args.lang)]
    for t in targets:
        if not os.path.exists(t):
            sys.exit("missing package: " + t)

    if args.restore:
        for t in targets:
            bak = t + ".original"
            if os.path.exists(bak):
                shutil.copyfile(bak, t); print("restored", os.path.basename(t))
            else:
                print("no backup for", os.path.basename(t), "- nothing to restore")
        return

    here = os.path.dirname(os.path.abspath(__file__))
    exe = get_decompressor(os.path.join(here, "tools"))

    if args.dump_fontlib:
        src = targets[0] + ".original" if os.path.exists(targets[0] + ".original") else targets[0]
        dec, tmp = decompress(exe, src)
        pkg = Pkg(dec)
        e = pkg.export("fonts_en.fonts_en")
        blob = pkg.data[e["off"]:e["off"] + e["size"]]
        g = blob.find(b"GFX")
        flen = struct.unpack_from("<I", blob, g + 4)[0]
        out = os.path.join(here, "fonts_en.gfx")
        open(out, "wb").write(blob[g:g + flen])
        shutil.rmtree(tmp, ignore_errors=True)
        print("wrote", out, "- open it in JPEXS Free Flash Decompiler and export the font 'Rockwell WGL' as TTF")
        return

    ttf = args.font or DEFAULT_TTF
    if not os.path.exists(ttf):
        sys.exit("Rockwell font not found at %s - pass --font <file.ttf> (see README for where to get it)" % ttf)
    fallback = DEFAULT_TTF if os.path.exists(DEFAULT_TTF) and os.path.abspath(DEFAULT_TTF) != os.path.abspath(ttf) else None

    for t in targets:
        name = os.path.basename(t)
        bak = t + ".original"
        if not os.path.exists(bak):
            if not is_compressed(t):
                sys.exit("%s is not a pristine (compressed) package and no .original backup exists - verify game files in Steam first" % name)
            shutil.copyfile(t, bak)
            print("backup:", os.path.basename(bak))
        print("patching", name)
        dec, tmp = decompress(exe, bak)
        pkg = Pkg(dec)
        rebuild_font(pkg, FONT_NAME, ttf, fallback, args.scale, print)
        open(t, "wb").write(pkg.data)
        shutil.rmtree(tmp, ignore_errors=True)
        print("  written: %s (%d bytes)" % (name, len(pkg.data)))
    print("done. Run with --restore to undo.")


if __name__ == "__main__":
    main()
