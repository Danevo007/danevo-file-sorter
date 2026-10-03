# ◢ Danevo File Sorter

A rule-based file organiser with a modern dark interface. Point it at a messy folder (your Downloads, for
example), describe how you want things sorted, and let it keep the folder tidy automatically from then on.

- **Ranked rules** - the first matching rule wins, so you control priority (drag the number or use ▲ ▼).
- **Sorts by name, not just extension** - a smart name parser groups a movie, its subtitles and its
  release-style variants under one title (`Inception.2010.1080p.BluRay.x264.mkv` +
  `Inception.2010.eng.srt` -> `Movies/Inception (2010)/`).
- **Rich conditions** - extension, content type (detected from the file's bytes), name keywords, regex,
  date in the file name, min/max size, minimum age. All conditions on a rule must match.
- **Smart destinations** - placeholders such as `{title}`, `{season}`, `{movie}`, `{year}`, `{ext}`,
  `{kind}`, `{name_year}` and your own regex groups.
- **Watch mode** - new files are sorted the moment they settle; runs quietly from the system tray and can
  start with Windows.
- **Safe by design** - live preview, one-click undo for every batch, never overwrites (adds `(1)`, `(2)`…),
  skips in-progress downloads, duplicates are moved aside (never deleted).
- **Extras** - ZIP-away rules for old files, SHA-256 duplicate detection, a "Test a name" tool.

## Quick start

```bash
git clone https://github.com/<your-username>/danevo-file-sorter.git
cd danevo-file-sorter
pip install -r requirements.txt
python danevo_file_sorter.py
```

Requires Python 3.10+. The tray icon needs `pystray` + `Pillow` (installed by `requirements.txt`);
without them the app still works and simply closes normally.

Prefer a ready-made app? Grab the installer or portable zip from the
[Releases](../../releases) page.

## How rules work

Rules are checked top to bottom; the first enabled rule that matches a file decides where it goes.

| Condition | Example |
|---|---|
| Extensions | `pdf, docx, xlsx` |
| Content type | `image, video, subtitle, archive…` (sniffed from the file when the extension is unknown) |
| Smart name pattern | TV episode · movie · any title |
| Name contains | `invoice, receipt` |
| Regex | `^(?P<client>.+?)_invoice` -> `{client}` |
| Date in name | `IMG-20240315-WA0001.jpg` -> `{name_year}/{name_month}` |
| Size / age | `≥ 2048 MB`, `older than 90 days` |

Destinations are folders inside the watched folder (or absolute paths), e.g. `TV Series/{title}/{season}`.
A rule can also **add to a ZIP** instead of moving (destination `Old Files/{year}` -> `Old Files/2026.zip`).
Existing folders are reused even when their casing or punctuation differs (`breaking bad` = `Breaking.Bad`).

Settings and rules live in `~/.danevo_file_sorter/` (`config.json`, `history.json`).

## Build the Windows app

See [BUILDING.md](BUILDING.md) for the one-click `build.bat`, the Inno Setup installer, and troubleshooting.
Pushing a version tag (`git tag v1.1.0 && git push origin v1.1.0`) builds and publishes both automatically
through GitHub Actions.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The sorting engine is independent of the GUI and is covered by headless tests in `tests/`.

## Project layout

```
danevo_file_sorter.py   app: engine + GUI + tray
make_icon.py            generates danevo.png / danevo.ico
build.bat               local Windows build
installer.iss           Inno Setup installer script
version_info.txt        Windows file properties
tests/                  headless engine tests
.github/workflows/      CI tests + Windows release build
```

## License

[MIT](LICENSE)
