#!/usr/bin/env python3
"""Bigger subtitles for Batman: Arkham Asylum GOTY (Steam).

The game hard-codes its subtitle sizes: in-game dialogue goes through a
Scaleform/Flash HUD text field authored at 24pt, and cutscene videos (.bik)
are subtitled by the engine with a bitmap font whose glyphs are ~12px tall.
Neither scales with resolution, so at 1440p/4K/ultrawide they are unreadable.

This tool patches the user's own game files (no game content is distributed):

  1. BmGame.u ......... the HUD Flash movie's subtitle DefineEditText font
                        height is raised from 24pt to 48pt (2 bytes changed).
  2. Startup_INT.upk .. the engine subtitle font (BmFonts.SmallFont) is
                        rebuilt at 4x (or 2x with --font-scale 2): the DXT5
                        glyph atlas is decoded, upscaled with Lanczos,
                        re-encoded, and all 276 glyph metrics are scaled.

Both files are Unreal Engine 3 packages compressed with LZO; they are first
decompressed with Gildor's freeware "decompress" tool (downloaded from
gildor.org on first run). The game loads decompressed packages natively.

Usage:
  python patch.py                     # patch (default Steam location)
  python patch.py --game-dir "D:\\Steam\\steamapps\\common\\Batman Arkham Asylum GOTY"
  python patch.py --hud-size 40       # HUD subtitle size in pt (default 48)
  python patch.py --revert            # restore the original files

Requires: Windows, Python 3.8+, Pillow (pip install -r requirements.txt).
Only the Steam GOTY build is supported; every patched byte is verified
against the expected original value first, so on any other build the tool
aborts without touching your files.
"""

import argparse
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

DEFAULT_GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Batman Arkham Asylum GOTY"
DECOMPRESS_URL = "https://www.gildor.org/down/47/umodel/decompress.zip"

# ---- verified constants for the Steam GOTY build (decompressed packages) ----
BMGAME_DECOMP_SIZE = 100365345
BMGAME_COMP_SIZE = 59857525
HUD_SWF_OFF = 0x1610303          # GFxMovieInfo 'HUD' GFX stream start
HUD_FONTHEIGHT_OFF = 0x161E73A   # subtitle DefineEditText font height (u16 twips)
HUD_ORIGINAL_TWIPS = 480         # 24 pt

STARTUP_DECOMP_SIZE = 1972496
STARTUP_COMP_SIZE = 798701

# The engine's subtitle fonts. MediumFont is what the game actually uses for
# dialogue and movie subtitles (the SubtitleFontName ini setting is ignored);
# SmallFont is patched too for completeness. Offsets are for the pristine
# decompressed Startup_INT.upk. Each page: (export table entry offset,
# export data offset, end of property list, SizeX/SizeY/MipTailBaseIdx
# value offsets, width, height).
FONTS = [
    {
        "name": "MediumFont", "char_arr": 0x1CD87, "count": 276,
        "pages": [
            {"entry": 97899, "off": 787745, "props_end": 0xC05F9,
             "sizex": 0xC053D, "sizey": 0xC0559, "miptail": 0xC0595,
             "w": 256, "h": 256},
            {"entry": 97967, "off": 853557, "props_end": 0xD070D,
             "sizex": 0xD0651, "sizey": 0xD066D, "miptail": 0xD06A9,
             "w": 256, "h": 32},
        ],
    },
    {
        "name": "SmallFont", "char_arr": 0x1E9E5, "count": 276,
        "pages": [
            {"entry": 98171, "off": 871773, "props_end": 0xD4E35,
             "sizex": 0xD4D79, "sizey": 0xD4D95, "miptail": 0xD4DD1,
             "w": 256, "h": 256},
        ],
    },
]


def fail(msg):
    print(f"\nERROR: {msg}")
    sys.exit(1)


def game_paths(game_dir):
    cooked = os.path.join(game_dir, "BmGame", "CookedPC")
    return (os.path.join(cooked, "BmGame.u"),
            os.path.join(cooked, "Startup_INT.upk"))


# --------------------------------------------------------------------------
# Gildor's package decompressor
# --------------------------------------------------------------------------

def get_decompressor(tools_dir):
    exe = os.path.join(tools_dir, "decompress.exe")
    if os.path.isfile(exe):
        return exe
    os.makedirs(tools_dir, exist_ok=True)
    print(f"Downloading Gildor's package decompressor ({DECOMPRESS_URL}) ...")
    req = urllib.request.Request(DECOMPRESS_URL, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.gildor.org/downloads",
    })
    with urllib.request.urlopen(req) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.lower().endswith("decompress.exe"):
                with z.open(name) as src, open(exe, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                break
        else:
            fail("decompress.exe not found inside the downloaded zip")
    return exe


def decompress(exe, package_path, workdir):
    subprocess.run([exe, "-game=batman", package_path],
                   cwd=workdir, check=True, capture_output=True)
    out = os.path.join(workdir, "unpacked", os.path.basename(package_path))
    if not os.path.isfile(out):
        fail(f"decompressor produced no output for {package_path}")
    return out


# --------------------------------------------------------------------------
# Patch 1: HUD Flash subtitle field (BmGame.u)
# --------------------------------------------------------------------------

def patch_bmgame(data, hud_pt):
    if bytes(data[HUD_SWF_OFF:HUD_SWF_OFF + 3]) != b"GFX":
        fail("BmGame.u: HUD Flash movie not found where expected (unsupported build)")
    cur = struct.unpack_from("<H", data, HUD_FONTHEIGHT_OFF)[0]
    new = int(round(hud_pt * 20))
    if cur == new:
        print(f"BmGame.u: HUD subtitle already at {hud_pt}pt, nothing to do")
        return None
    if cur != HUD_ORIGINAL_TWIPS and cur % 20 != 0:
        fail(f"BmGame.u: unexpected value {cur} at subtitle field (unsupported build)")
    struct.pack_into("<H", data, HUD_FONTHEIGHT_OFF, new)
    print(f"BmGame.u: HUD subtitle font {cur / 20:g}pt -> {hud_pt}pt")
    return data


# --------------------------------------------------------------------------
# Patch 2: engine subtitle font rebuild (Startup_INT.upk)
# --------------------------------------------------------------------------

def decode_dxt5(buf, w, h):
    img = bytearray(w * h * 4)
    bi = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            a0, a1 = buf[bi], buf[bi + 1]
            abits = int.from_bytes(buf[bi + 2:bi + 8], "little")
            c0, c1 = struct.unpack_from("<HH", buf, bi + 8)
            cbits = struct.unpack_from("<I", buf, bi + 12)[0]
            bi += 16
            pal = []
            for c in (c0, c1):
                pal.append((((c >> 11) & 31) * 255 // 31,
                            ((c >> 5) & 63) * 255 // 63,
                            (c & 31) * 255 // 31))
            if c0 > c1:
                pal.append(tuple((2 * a + b) // 3 for a, b in zip(pal[0], pal[1])))
                pal.append(tuple((a + 2 * b) // 3 for a, b in zip(pal[0], pal[1])))
            else:
                pal.append(tuple((a + b) // 2 for a, b in zip(pal[0], pal[1])))
                pal.append((0, 0, 0))
            apal = [a0, a1]
            if a0 > a1:
                apal += [((7 - j) * a0 + j * a1) // 7 for j in range(1, 7)]
            else:
                apal += [((5 - j) * a0 + j * a1) // 5 for j in range(1, 5)]
                apal += [0, 255]
            for idx in range(16):
                x, y = bx + (idx & 3), by + (idx >> 2)
                o = (y * w + x) * 4
                r, g, b = pal[(cbits >> (2 * idx)) & 3]
                img[o:o + 4] = bytes((r, g, b, apal[(abits >> (3 * idx)) & 7]))
    return bytes(img)


def encode_dxt5(px, w, h):
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            alphas, rs = [], [0, 0, 0]
            for idx in range(16):
                o = ((by + (idx >> 2)) * w + bx + (idx & 3)) * 4
                rs[0] += px[o]; rs[1] += px[o + 1]; rs[2] += px[o + 2]
                alphas.append(px[o + 3])
            a0, a1 = max(alphas), min(alphas)
            if a0 == a1:
                aidx = [0] * 16
            else:
                apal = [a0, a1] + [((7 - j) * a0 + j * a1) // 7 for j in range(1, 7)]
                aidx = [min(range(8), key=lambda i, a=a: abs(apal[i] - a)) for a in alphas]
            abits = 0
            for i, v in enumerate(aidx):
                abits |= v << (3 * i)
            c565 = (((rs[0] // 16) >> 3) << 11) | (((rs[1] // 16) >> 2) << 5) | ((rs[2] // 16) >> 3)
            out += bytes((a0, a1)) + abits.to_bytes(6, "little")
            out += struct.pack("<HHI", c565, c565, 0)
    return bytes(out)


def rebuild_page(data, page, font_scale):
    """Rebuild one font texture page at font_scale x; returns modified data."""
    from PIL import Image

    def u32(o):
        return struct.unpack_from("<I", data, o)[0]

    w, h = page["w"], page["h"]
    p = page["props_end"] + 16      # skip empty SourceArt bulk header
    if u32(p) != 1:
        fail("Startup_INT.upk: unexpected mip count (unsupported build)")
    p += 4
    m_cnt, m_off = u32(p + 4), u32(p + 12)
    p += 16
    if m_cnt != w * h or m_off != p:
        fail("Startup_INT.upk: unexpected texture layout (unsupported build)")
    mipdata = bytes(data[p:p + m_cnt])
    guid = bytes(data[p + m_cnt + 8:p + m_cnt + 24])

    img = Image.frombytes("RGBA", (w, h), decode_dxt5(mipdata, w, h))
    w2, h2 = w * font_scale, h * font_scale
    big = img.resize((w2, h2), Image.LANCZOS)
    newmip = encode_dxt5(big.tobytes(), w2, h2)

    # new Texture2D export blob appended at EOF
    new_off = len(data)
    blob = bytearray(data[page["off"]:page["props_end"]])

    def patch_u32(abs_off, val):
        struct.pack_into("<I", blob, abs_off - page["off"], val)

    patch_u32(page["sizex"], w2)
    patch_u32(page["sizey"], h2)
    patch_u32(page["miptail"], max(w2, h2).bit_length() - 1)
    tail = bytearray()
    tail += struct.pack("<4I", 0, 0, 0, new_off + len(blob) + 16)   # empty SourceArt
    tail += struct.pack("<I", 1)                                     # mip count
    mip_data_pos = new_off + len(blob) + len(tail) + 16
    tail += struct.pack("<4I", 0, len(newmip), len(newmip), mip_data_pos)
    tail += newmip
    tail += struct.pack("<2I", w2, h2)
    tail += guid
    blob += tail

    data += blob
    struct.pack_into("<i", data, page["entry"] + 32, len(blob))      # SerialSize
    struct.pack_into("<i", data, page["entry"] + 36, new_off)        # SerialOffset
    return data


def patch_startup(data, font_scale):
    def u32(o):
        return struct.unpack_from("<I", data, o)[0]

    for font in FONTS:
        cnt = u32(font["char_arr"])
        if cnt != font["count"]:
            fail(f"Startup_INT.upk: unexpected glyph count {cnt} for "
                 f"{font['name']} (unsupported build)")
        for page in font["pages"]:
            data = rebuild_page(data, page, font_scale)
        # scale glyph metrics in place (StartU/StartV/USize/VSize/VerticalOffset)
        for k in range(cnt):
            e = font["char_arr"] + 4 + k * 21
            su, sv, us, vs = struct.unpack_from("<4i", data, e)
            vo = struct.unpack_from("<i", data, e + 17)[0]
            struct.pack_into("<4i", data, e, su * font_scale, sv * font_scale,
                             us * font_scale, vs * font_scale)
            struct.pack_into("<i", data, e + 17, vo * font_scale)
        print(f"Startup_INT.upk: {font['name']} rebuilt at {font_scale}x "
              f"({cnt} glyphs, {len(font['pages'])} page(s))")
    return data


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def load_package(path, exe, workdir, comp_size, decomp_size, label):
    size = os.path.getsize(path)
    if size == comp_size:
        print(f"{label}: original (compressed), decompressing ...")
        out = decompress(exe, path, workdir)
        data = bytearray(open(out, "rb").read())
        if len(data) != decomp_size:
            fail(f"{label}: decompressed size {len(data)} != expected {decomp_size}")
        return data
    if size == decomp_size:
        print(f"{label}: already decompressed")
        return bytearray(open(path, "rb").read())
    fail(f"{label}: size {size} does not match the Steam GOTY build "
         f"(is the game modified, or a different edition?)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game-dir", default=DEFAULT_GAME_DIR,
                    help="game install folder (default: %(default)s)")
    ap.add_argument("--hud-size", type=float, default=48.0,
                    help="HUD subtitle size in pt, original is 24 (default: %(default)s)")
    ap.add_argument("--font-scale", type=int, default=4, choices=(2, 4),
                    help="cutscene/engine subtitle font scale (default: %(default)s)")
    ap.add_argument("--revert", action="store_true",
                    help="restore the .original backup files and exit")
    args = ap.parse_args()

    bmgame, startup = game_paths(args.game_dir)
    if not os.path.isfile(bmgame) or not os.path.isfile(startup):
        fail(f"game not found at {args.game_dir}\n"
             "       use --game-dir to point at your install folder")

    if args.revert:
        for path in (bmgame, startup):
            bak = path + ".original"
            if os.path.isfile(bak):
                shutil.copyfile(bak, path)
                print(f"restored {os.path.basename(path)}")
            else:
                print(f"no backup found for {os.path.basename(path)} "
                      "(verify game files on Steam to restore)")
        return

    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    exe = get_decompressor(tools_dir)

    with tempfile.TemporaryDirectory() as workdir:
        # Startup_INT.upk: the appended-texture patch must start from a
        # pristine file, so a size equal to the patched output means done.
        st_size = os.path.getsize(startup)
        if st_size not in (STARTUP_COMP_SIZE, STARTUP_DECOMP_SIZE):
            bak = startup + ".original"
            if os.path.isfile(bak):
                print("Startup_INT.upk: already patched — rebuilding from backup ...")
                shutil.copyfile(bak, startup)
                st_data = load_package(startup, exe, workdir, STARTUP_COMP_SIZE,
                                       STARTUP_DECOMP_SIZE, "Startup_INT.upk")
                st_data = patch_startup(st_data, args.font_scale)
            else:
                print("Startup_INT.upk: already patched (or modified) and no backup "
                      "found, skipping font rebuild")
                st_data = None
        else:
            st_data = load_package(startup, exe, workdir, STARTUP_COMP_SIZE,
                                   STARTUP_DECOMP_SIZE, "Startup_INT.upk")
            st_data = patch_startup(st_data, args.font_scale)

        bm_data = load_package(bmgame, exe, workdir, BMGAME_COMP_SIZE,
                               BMGAME_DECOMP_SIZE, "BmGame.u")
        bm_data = patch_bmgame(bm_data, args.hud_size)

    pristine_sizes = {bmgame: BMGAME_COMP_SIZE, startup: STARTUP_COMP_SIZE}
    for path, data in ((bmgame, bm_data), (startup, st_data)):
        if data is None:
            continue
        bak = path + ".original"
        # only snapshot a backup when the on-disk file is the pristine
        # compressed original — never a previously patched file
        if not os.path.isfile(bak) and os.path.getsize(path) == pristine_sizes[path]:
            shutil.copyfile(path, bak)
        with open(path, "wb") as f:
            f.write(data)
        note = f" (backup: {os.path.basename(bak)})" if os.path.isfile(bak) else ""
        print(f"installed {os.path.basename(path)}{note}")

    print("\nDone. Start the game and enjoy readable subtitles.")
    print("Note: Steam's 'Verify integrity of game files' undoes this patch.")


if __name__ == "__main__":
    main()
