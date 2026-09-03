# Arkham Asylum Subtitle Fix

Bigger, sharp subtitles for **Batman: Arkham Asylum GOTY (Steam)** on modern displays.

*Legendas maiores e nítidas para o Batman: Arkham Asylum GOTY (Steam). [Instruções em português](#em-português).*

## The problem

The game draws subtitles at a fixed pixel size chosen in 2009 and offers no
setting to change it. At 1440p, 4K, or on an ultrawide they are tiny to the
point of being unreadable — a complaint found in forum threads going back over
a decade, always answered with "there is no fix or mod for this".

## Quick start

1. Download `ArkhamSubtitleFix.exe` from the [latest release](../../releases/latest).
2. Close the game and double-click the `.exe`.
3. It finds your Steam install by itself. Press `1` for the recommended 2x size.
4. Start the game.

Windows SmartScreen may show "Windows protected your PC" the first time,
because the `.exe` is not code-signed: click *More info* → *Run anyway*.

To undo: run it again and press `4`. Steam's *Verify integrity of game files*
also restores the originals (and therefore removes the patch).

No account, no installer, nothing else to download. The tool only touches two
files inside the game folder and keeps a `.original` backup of each.

## What it does

Dialogue subtitles are drawn by the engine with a bitmap font,
`BmFonts.MediumFont` — the same font used for the "Loading" text and the
"press X to skip" prompt in cutscenes. Its glyphs are 15 px tall, and the
`SubtitleFontName` setting in the ini is ignored by the game, so the only way
to get bigger subtitles is a bigger font.

The patcher **re-renders that font at 2x** (1.5x and 2.5x are offered too)
from the vector Rockwell font that ships inside the game's own Scaleform font
library, so the result is sharp, not a blurry upscale. It writes the new font
into the two game packages that carry a copy of it (`Startup_INT.upk` and
`CommonGame_LOC_INT.upk`).

Side effect, which most people will consider an improvement: the "Loading"
text and the cutscene skip prompt get bigger too.

No game files are distributed by this repository or its releases. The patcher
modifies **your own** copy of the game, parses and verifies every structure
before writing, and aborts without touching anything if a file does not look
like the Steam GOTY build. It works with subtitle translation mods (e.g. the
PT-BR fan translation): they replace text, this replaces a font.

## Running from source

Needs Windows and [Python 3.8+](https://www.python.org/downloads/) (tick
*"Add python.exe to PATH"* when installing).

```
git clone https://github.com/tifanyporto/arkham-asylum-subtitle-fix.git
cd arkham-asylum-subtitle-fix
pip install -r requirements.txt
python patch.py
```

Command-line options:

```
python patch.py --apply                      # 2x, auto-detect Steam
python patch.py --apply --scale 1.5          # any scale from 1.2 to 3
python patch.py --apply --game-dir "D:\SteamLibrary\steamapps\common\Batman Arkham Asylum GOTY"
python patch.py --apply --font Rockwell.ttf  # render from a TrueType file instead
python patch.py --restore
```

`tools\decompress.exe` is Gildor's freeware UE3 package decompressor from
[gildor.org](https://www.gildor.org/downloads); the `.exe` release has it bundled.

### Building the .exe

```
pip install pyinstaller
build_exe.bat
```

produces `dist\ArkhamSubtitleFix.exe`.

## Technical notes

- Both packages are LZO-compressed UE3 packages (version 576, licensee 21).
  They are decompressed once; the engine loads decompressed packages natively.
- The font is a `UFont` with 276 `FontCharacter` entries (StartU, StartV,
  USize, VSize, page, VerticalOffset) and a `CharRemap` map, plus one or two
  DXT5 texture pages. The patcher parses the `DefineFont3` glyph outlines of
  "Rockwell WGL" from the game's `fonts_en` Scaleform library, rasterizes each
  glyph with a scanline filler at the size whose cap height matches the
  original scaled, packs them into a 1024-wide atlas, re-encodes DXT5,
  appends the new texture as a relocated export at the end of the package and
  repoints its export-table entry, and rewrites the metrics in place. Nothing
  else in the package moves.
- The same font object exists in `Startup_INT.upk` and in
  `CommonGame_LOC_INT.upk`; the engine keeps whichever it loads first, so both
  are patched.
- Things that do **not** control the subtitle size, verified in-game: the
  `SubtitleFontName` ini setting, `BmFonts.SmallFont`, and the Scaleform text
  fields inside the HUD movie in `BmGame.u`.

## Em português

1. Baixe o `ArkhamSubtitleFix.exe` na [página de releases](../../releases/latest).
2. Feche o jogo e dê dois cliques no `.exe`.
3. Ele acha a pasta do jogo sozinho. Aperte `1` para o tamanho recomendado (2x).
4. Abra o jogo.

Se o Windows mostrar "O Windows protegeu o computador", clique em *Mais
informações* → *Executar assim mesmo*. O aviso aparece porque o `.exe` não
tem assinatura digital.

Para desfazer, rode de novo e aperte `4`. O "Verificar integridade dos
arquivos" do Steam também restaura os originais (e remove o patch).

Funciona junto com a tradução PT-BR: ela troca os textos, isto troca a fonte.

## Credits

- [Gildor's decompress](https://www.gildor.org/) — UE3 package decompression
- Rockwell is a typeface of Monotype; the glyphs are read from the user's own game files and no font file is distributed

## License

The patcher script is released under the [MIT License](LICENSE). It contains
no assets from the game.
