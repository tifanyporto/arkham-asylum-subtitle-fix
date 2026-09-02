# Arkham Asylum Subtitle Fix

Bigger, readable subtitles for **Batman: Arkham Asylum GOTY (Steam)** on modern displays.

## The problem

The game renders subtitles at a fixed pixel size chosen in 2009 and offers no
setting to change it. At 1440p, 4K, or on an ultrawide, subtitles are tiny to
the point of being unreadable — a complaint you can find in forum threads going
back over a decade, always answered with "there is no fix or mod for this".

There was no fix because there is no config value for it: the sizes are baked
into the game's binary assets, in two different places.

## What this patch does

| Where you see it | How it's stored | What the patch does |
|---|---|---|
| In-game dialogue and real-time cutscenes | A 24pt text field inside the HUD's Scaleform (Flash) movie, embedded in `BmGame.u` | Raises the field to 48pt (2-byte change; the Scaleform fonts are vector, so it stays sharp) |
| Pre-rendered video cutscenes (`.bik`) and other engine subtitles | A bitmap font (`BmFonts.SmallFont`) with ~12px glyphs inside `Startup_INT.upk` | Rebuilds the font at 4x: decodes the DXT5 glyph atlas, upscales it 256→1024 (Lanczos), re-encodes it, and scales all 276 glyph metrics |

No game files are distributed by this repository — the patcher modifies **your
own** copy of the game, and verifies every expected byte before writing
anything, so on an unsupported version it aborts without touching your files.
Backups are created automatically.

Compatible with subtitle translation mods (e.g. PT-BR translations that
replace `Localization\INT` files) — this patch does not touch localization.

## Requirements

- Windows
- Batman: Arkham Asylum **GOTY** — **Steam** version
- [Python 3.8+](https://www.python.org/downloads/) (check *"Add python.exe to PATH"* during install)
- The Pillow imaging library (installed in step 2 below)

## How to apply

1. **Close the game**, then open a terminal (press `Win+R`, type `cmd`, Enter)
   and clone or [download](../../archive/refs/heads/main.zip) this repository:

   ```
   git clone https://github.com/YOURUSER/arkham-asylum-subtitle-fix.git
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

   On first run the tool downloads Gildor's freeware UE3 package
   decompressor (~100 KB) from [gildor.org](https://www.gildor.org/downloads),
   the standard tool of the Unreal modding community for 20 years.

4. Start the game. Subtitles are now twice the size — both in-game dialogue
   and the video cutscenes (the first line in the intro video appears about
   30 seconds in, if you want a quick check).

### Tuning

Not to your taste? Both sizes are adjustable. The HUD (in-game dialogue)
subtitle takes any point size — the original is 24:

```
python patch.py --hud-size 36
```

The cutscene/engine font supports 4x (default) or the milder 2x:

```
python patch.py --font-scale 2
```

Re-running the patcher applies the new sizes (it rebuilds from the backups
it kept), no need to revert first.

## How to revert

```
python patch.py --revert
```

This restores the `.original` backups created on the first run. Alternatively,
**Verify integrity of game files** on Steam re-downloads the originals — which
also means Steam's verification will *undo* this patch (and any other mod), so
just re-run the patcher if that happens.

## Technical notes

- Both packages are LZO-compressed UE3 packages (version 576, licensee 21).
  They are decompressed once; the game engine loads decompressed packages
  natively, they are simply larger on disk.
- The HUD subtitle field is `DefineEditText` character 171 (sprite 173,
  exported as `Subtitles`, driven by `rs.hud.Subtitle.SetText` — which
  receives a size argument from the game and ignores it) inside the
  `GFxMovieInfo 'HUD'` asset of `BmGame.u`.
- The engine font rebuild appends the new 512x512 texture as a relocated
  export at the end of `Startup_INT.upk` and repoints the export table entry
  (`SerialSize`/`SerialOffset`), so no other offset in the package moves.
  Glyph metrics (`FontCharacter`: StartU/StartV/USize/VSize/VerticalOffset)
  are doubled in place.
- The interview-tape subtitles in the bio menus use a third, separate text
  field (in the `CharacterBio` Scaleform movie) that renders at menu scale
  and is left untouched.

## Credits

- [Gildor's decompress & UE Viewer](https://www.gildor.org/) — UE3 package tooling
- [JPEXS Free Flash Decompiler](https://github.com/jindrapetrik/jpexs-decompiler) — used to reverse-engineer the HUD movie

## License

The patcher script is released under the [MIT License](LICENSE). It contains
no assets from the game.
