# Building Danevo File Sorter (Windows)

## Folder contents
| File | Purpose |
|---|---|
| `danevo_file_sorter.py` | the app |
| `make_icon.py` | draws `danevo.png` + multi-size `danevo.ico` (already generated for you) |
| `build.bat` | one-click build: venv -> dependencies -> icon -> PyInstaller |
| `version_info.txt` | name / company / version shown in the .exe's Properties > Details |
| `installer.iss` | Inno Setup script that wraps the build into a real `Setup.exe` |
| `requirements.txt` | customtkinter, pystray (tray), Pillow |

## 1. Build the .exe
1. Install Python 3.10+ from python.org (tick **Add python.exe to PATH**).
2. Double-click `build.bat`  (or run `build.bat onefile` for a single .exe).
3. Result:
   - folder build (recommended): `dist\Danevo File Sorter\Danevo File Sorter.exe`
   - one-file build: `dist\Danevo File Sorter.exe`

Folder vs one-file: the folder build starts instantly and triggers far fewer antivirus
false-positives. One-file is easier to share but unpacks itself on every launch (slow start).

## 2. Make a proper installer (recommended for sharing)
1. Install Inno Setup (free): https://jrsoftware.org/isinfo.php
2. Run `build.bat` (folder mode), open `installer.iss`, press **Compile** (Ctrl+F9).
3. You get `installer\DanevoFileSorter-Setup-1.2.0.exe` with Start-menu entry, optional
   desktop shortcut, optional "start with Windows", and a clean uninstaller.
   It installs per-user, so no admin rights are needed.

## 3. Test checklist before you ship
- Launch, add a test folder in Settings, click Preview -> Sort now -> Undo.
- Toggle "Auto-sort new files", drop a file in the folder, watch it move.
- Close the window: the app should keep running in the system tray (right-click it).
- Reboot (if "start with Windows" is enabled): it should appear in the tray, window hidden.

## Troubleshooting
- **Blank/unstyled window or missing theme errors**: the `--collect-all customtkinter` flag
  is required (already in build.bat).
- **No tray icon**: make sure `pystray` and `Pillow` installed; `--hidden-import pystray._win32` is needed.
- **SmartScreen / antivirus warning**: normal for unsigned apps. Fix properly by code-signing
  the .exe and installer (an OV/EV certificate or Azure Trusted Signing), or submit the file to
  Microsoft/your AV vendor as a false positive.
- **Rebuilding**: delete the `build`, `dist` and `.venv` folders if something looks stale.
- **Bump the version**: edit `APP_VERSION` in the .py, `version_info.txt` (2 places) and `#define MyAppVersion` in installer.iss.

## Where the app stores things
`C:\Users\<you>\.danevo_file_sorter\` -> `config.json` (rules + settings), `history.json` (undo log).
Settings from the first version (`.downloads_sorter`) are migrated automatically.
Uninstalling does not delete these, so your rules survive reinstalls.

## Name-based sorting (v1.1)
Rules can use a **Smart name pattern** instead of relying on extensions:
- *TV episode* - recognises `S01E02`, `1x02`, `Season 1`, release junk, `[groups]`, `www.site.com -` prefixes.
- *Movie* - a title plus a year and/or quality tags (`1080p`, `BluRay`, `x264`...).
- *Any title* - group by the cleaned title only.
Videos and their subtitles (any language tail like `.en.forced`) share one title, so they always land together.
Placeholders: `{title} {movie} {title_year} {season} {episode}`. Existing folders are reused even if their
casing/punctuation differ ("breaking bad" == "Breaking.Bad", "Season 2" == "Season 02").
Use **Test a name** on the Rules page to preview how any file name is parsed and routed.
