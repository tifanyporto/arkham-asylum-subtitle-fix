# Arkham Asylum Subtitle Fix

Bigger, sharp subtitles for **Batman: Arkham Asylum GOTY (Steam)** on modern displays.

## The problem

The game draws subtitles at a fixed pixel size chosen in 2009 and offers no
setting to change it. At 1440p, 4K, or on an ultrawide they are tiny to the
point of being unreadable — a complaint found in forum threads going back over
a decade, always answered with "there is no fix or mod for this".

## What this does

Dialogue subtitles are drawn by the engine with a bitmap font,
`BmFonts.MediumFont` — the same font used for the "Loading" text and the
"press X to skip" prompt in cutscenes. Its glyphs are 15 px tall, and the
`SubtitleFontName` setting in the ini is ignored by the game, so the only way
to get bigger subtitles is a bigger font.

The patcher **re-renders that font at 2x** (any scale from 1.2 to 3 works) from
a Rockwell TrueType font — the typeface the game uses — so the result is sharp,
not a blurry upscale. It writes the new font into the two game packages that
carry a copy of it (`Startup_INT.upk` and `CommonGame_LOC_INT.upk`).

Side effect, which most people will consider an improvement: the "Loading"
text and the cutscene skip prompt get bigger too.

No game files are distributed by this repository. The patcher modifies **your
own** copy of the game, parses and verifies every structure before writing,
and keeps a `.original` backup of each file it touches. It works with subtitle
translation mods (e.g. the PT-BR fan translation): they replace text, this
replaces a font.

## Requirements

- Windows
- Batman: Arkham Asylum **GOTY** — **Steam** version
- [Python 3.8+](https://www.python.org/downloads/) (check *"Add python.exe to PATH"* during install)
- The Pillow imaging library (installed in step 2 below)
- A **Rockwell** TrueType font. If Microsoft Office is installed you already
  have it at `C:\Windows\Fonts\ROCK.TTF` and the patcher finds it by itself.
  Otherwise see [Getting the font](#getting-the-font).

## How to apply

1. **Close the game**, open a terminal (`Win+R`, type `cmd`, Enter) and clone
   or [download](../../archive/refs/heads/main.zip) this repository:

   ```
   git clone https://github.com/tifanyporto/arkham-asylum-subtitle-fix.git
   cd arkham-asylum-subtitle-fix
   ```

2. Install the one dependency:

   ```
   pip install -r requirements.txt
   ```

3. Run the patcher:

   ```
   python patch.py
   ```

   If your game is not in the default Steam location, point at it:

   ```
   python patch.py --game-dir "D:\SteamLibrary\steamapps\common\Batman Arkham Asylum GOTY"
   ```

   On first run the tool downloads Gildor's freeware UE3 package decompressor
   (~100 KB) from [gildor.org](https://www.gildor.org/downloads).

4. Start the game. Subtitles are now twice the size.

### Tuning

```
python patch.py --scale 1.5
python patch.py --scale 2.5
```

Re-running the patcher rebuilds from the backups it kept, no need to revert
first.

### Getting the font

Rockwell ships with Microsoft Office. If you don't have it, the game itself
contains the vector version of the font; extract it once:

```
python patch.py --dump-fontlib
```

This writes `fonts_en.gfx` next to the script. Open it in
[JPEXS Free Flash Decompiler](https://github.com/jindrapetrik/jpexs-decompiler)
(needs Java), go to *fonts → Rockwell WGL*, choose *Export selection → TTF*,
then run:

```
python patch.py --font "Rockwell WGL.ttf"
```

## How to revert

```
python patch.py --restore
```

This restores the `.original` backups. Alternatively, **Verify integrity of
game files** on Steam re-downloads the originals — which also means Steam's
verification *undoes* this patch, so just re-run the patcher if that happens.

## Technical notes

- Both packages are LZO-compressed UE3 packages (version 576, licensee 21).
  They are decompressed once; the engine loads decompressed packages natively.
- The font is a `UFont` with 276 `FontCharacter` entries (StartU, StartV,
  USize, VSize, page, VerticalOffset) and a `CharRemap` map, plus one or two
  DXT5 texture pages. The patcher renders each glyph with the TrueType font at
  the size whose cap height matches the original scaled, packs them into a
  1024-wide atlas, re-encodes DXT5, appends the new texture as a relocated
  export at the end of the package and repoints its export-table entry, and
  rewrites the metrics in place. Nothing else in the package moves.
- The same font object exists in `Startup_INT.upk` and in
  `CommonGame_LOC_INT.upk`; the engine keeps whichever it loads first, so both
  are patched.
- Things that do **not** control the subtitle size, verified in-game: the
  `SubtitleFontName` ini setting, `BmFonts.SmallFont`, and the Scaleform text
  fields inside the HUD movie in `BmGame.u`.

## Credits

- [Gildor's decompress](https://www.gildor.org/) — UE3 package decompression
- [JPEXS Free Flash Decompiler](https://github.com/jindrapetrik/jpexs-decompiler) — font extraction
- Rockwell is a typeface of Monotype; no font file is distributed here

## License

The patcher script is released under the [MIT License](LICENSE). It contains
no assets from the game.
