import os
import time
import zipfile

import pytest

import danevo_file_sorter as d


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(d, "HISTORY_FILE", tmp_path / "app" / "history.json")
    root = tmp_path / "dl"
    root.mkdir()
    cfg = d.Config(rules=d.default_rules())
    cfg.settings.folders = [d.Folder(str(root))]
    cfg.settings.settle_seconds = 0
    return root, cfg, d.Sorter(cfg)


def make(root, name, data=None, age_days=0):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if data is not None else name.encode())
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
    return p


def dests(sorter, rel_to=None):
    return {os.path.basename(m.src): os.path.relpath(m.dest_dir, rel_to or m.root).replace("\\", "/")
            for m in sorter.plan()}


# ---- name parsing ---------------------------------------------------------
@pytest.mark.parametrize("name,title,year,season,episode", [
    ("Breaking.Bad.S02E05.720p.WEB-DL.mkv", "Breaking Bad", None, 2, 5),
    ("Breaking Bad - S02E05.en.srt", "Breaking Bad", None, 2, 5),
    ("breaking_bad_s02e06.mkv", "Breaking Bad", None, 2, 6),
    ("Inception.2010.1080p.BluRay.x264-YIFY.eng.forced.srt", "Inception", 2010, None, None),
    ("[YTS.MX] The Matrix (1999) [1080p].mp4", "The Matrix", 1999, None, None),
    ("www.TamilMV.com - Dune Part Two 2024 2160p WEB-DL.mkv", "Dune Part Two", 2024, None, None),
    ("Blade.Runner.2049.2017.1080p.mkv", "Blade Runner 2049", 2017, None, None),
    ("1917.2019.1080p.BluRay.mkv", "1917", 2019, None, None),
    ("Money.Heist.1x03.mkv", "Money Heist", None, 1, 3),
    ("Show Name Season 3 Complete.mkv", "Show Name", None, 3, None),
])
def test_parse_media(name, title, year, season, episode):
    info = d.parse_media(name)
    assert (info["title"], info["year"], info["season"], info["episode"]) == (title, year, season, episode)


def test_date_in_name():
    assert d.date_in_name("IMG-20240315-WA0001.jpg") == ("2024", "03", "15")
    assert d.date_in_name("scan 15.03.2024.pdf") == ("2024", "03", "15")
    assert d.date_in_name("nothing.pdf") is None


# ---- routing --------------------------------------------------------------
def test_series_movies_and_subtitles_group_by_title(env):
    root, cfg, s = env
    for n in ["Breaking.Bad.S02E05.720p.mkv", "Breaking Bad - S02E05.en.srt",
              "Inception.2010.1080p.x264.mkv", "Inception.2010.1080p.x264.eng.srt"]:
        make(root, n)
    r = dests(s)
    assert r["Breaking.Bad.S02E05.720p.mkv"] == r["Breaking Bad - S02E05.en.srt"] == "TV Series/Breaking Bad/Season 02"
    assert r["Inception.2010.1080p.x264.mkv"] == r["Inception.2010.1080p.x264.eng.srt"] == "Movies/Inception (2010)"


def test_existing_folder_is_reused_and_late_subtitle_joins_movie(env):
    root, cfg, s = env
    (root / "tv series" / "breaking bad" / "season 2").mkdir(parents=True)
    (root / "Movies" / "Inception (2010)").mkdir(parents=True)
    make(root, "Breaking.Bad.S02E09.mkv")
    make(root, "Inception.srt")
    r = dests(s)
    assert r["Breaking.Bad.S02E09.mkv"] == "tv series/breaking bad/season 2"
    assert r["Inception.srt"] == "Movies/Inception (2010)"


def test_rule_priority_first_match_wins(env):
    root, cfg, s = env
    make(root, "setup.exe")
    cfg.rules.insert(0, d.Rule("Everything exe", True, "exe", target="Programs"))
    assert dests(s)["setup.exe"] == "Programs"


def test_content_sniffing_without_extension(env):
    root, cfg, s = env
    make(root, "mystery", b"%PDF-1.4 hi")
    assert dests(s)["mystery"] == "By Content/Document"


def test_skips_partial_downloads_and_recent_files(env):
    root, cfg, s = env
    make(root, "movie.mkv.crdownload")
    cfg.settings.settle_seconds = 3600
    make(root, "fresh.pdf")
    assert dests(s) == {}


# ---- execute / undo -------------------------------------------------------
def test_execute_and_undo_roundtrip_cleans_empty_dirs(env):
    root, cfg, s = env
    make(root, "report.pdf")
    moves = s.plan()
    assert s.execute(moves) == (1, 0)
    assert not (root / "report.pdf").exists()
    assert s.undo_last() == 1
    assert (root / "report.pdf").exists()
    assert not (root / "Documents").exists()


def test_name_clash_gets_suffix(env):
    root, cfg, s = env
    make(root, "Documents/2026/report.pdf", b"old")
    make(root, "report.pdf", b"new")
    year = str(time.localtime().tm_year)
    s.execute(s.plan())
    assert (root / "Documents" / year / "report (1).pdf").exists() or (root / "Documents" / "2026" / "report (1).pdf").exists()


def test_duplicates_moved_aside_original_kept(env):
    root, cfg, s = env
    cfg.settings.duplicate_mode = "move"
    make(root, "Documents/2026/original.pdf", b"samebytes")
    make(root, "copy.pdf", b"samebytes")
    assert dests(s)["copy.pdf"] == "Duplicates"


def test_zip_action_and_undo(env):
    root, cfg, s = env
    for r in cfg.rules:
        if r.action == "zip":
            r.enabled = True
    make(root, "ancient.xyz", b"old data", age_days=200)
    s.execute(s.plan())
    zips = list(root.rglob("*.zip"))
    assert len(zips) == 1 and "ancient.xyz" in zipfile.ZipFile(zips[0]).namelist()
    assert s.undo_last() == 1
    assert (root / "ancient.xyz").read_bytes() == b"old data"
    assert not list(root.rglob("*.zip"))


def test_explain(env):
    _, _, s = env
    rule, dest = s.explain("Breaking.Bad.S02E07.1080p.mkv")
    assert rule.startswith("TV series") and dest.endswith("Breaking Bad/Season 02/")
    assert s.explain("random.xyz") is None


# ---- anime-style names ------------------------------------------------------
@pytest.mark.parametrize("name,title,season,episode", [
    ("The Seven Deadly Sins - 05.mkv", "The Seven Deadly Sins", None, 5),
    ("[SubsPlease] Nanatsu no Taizai - 05 (1080p) [ABCD1234].mkv", "Nanatsu no Taizai", None, 5),
    ("Seven.Deadly.Sins.Ep05.720p.mkv", "Seven Deadly Sins", None, 5),
    ("The Seven Deadly Sins S2 - 05.mkv", "The Seven Deadly Sins", 2, 5),
    ("The Seven Deadly Sins 2nd Season - 07v2.mkv", "The Seven Deadly Sins", 2, 7),
])
def test_parse_anime(name, title, season, episode):
    info = d.parse_media(name)
    assert (info["title"], info["season"], info["episode"]) == (title, season, episode)
    assert info["series"]


def test_anime_files_get_series_folder_with_default_season(env):
    root, cfg, s = env
    make(root, "[Group] The Seven Deadly Sins - 01 [1080p].mkv")
    make(root, "[Group] The Seven Deadly Sins - 02 [1080p].mkv")
    r = dests(s)
    assert set(r.values()) == {"TV Series/The Seven Deadly Sins/Season 01"}


# ---- subfolders (the "folder full of episodes" case) ------------------------
def test_subfolders_ignored_by_default(env):
    root, cfg, s = env
    make(root, "Seven Deadly Sins/The Seven Deadly Sins - 01.mkv")
    assert dests(s) == {}


def test_subfolder_episodes_are_regrouped_and_emptied_folder_removed(env):
    root, cfg, s = env
    cfg.settings.folders[0].depth = 1
    for n in range(1, 4):
        make(root, f"Seven Deadly Sins/The Seven Deadly Sins - {n:02d}.mkv")
    moves = s.plan()
    assert {os.path.relpath(m.dest_dir, root).replace("\\", "/") for m in moves} == {
        "TV Series/The Seven Deadly Sins/Season 01"}
    s.execute(moves)
    assert len(list((root / "TV Series" / "The Seven Deadly Sins" / "Season 01").iterdir())) == 3
    assert not (root / "Seven Deadly Sins").exists()
    assert s.plan() == []  # organised files are left alone on the next scan
    assert s.undo_last() == 3
    assert (root / "Seven Deadly Sins" / "The Seven Deadly Sins - 02.mkv").exists()
    assert not (root / "TV Series").exists()


def test_weak_filenames_borrow_title_and_season_from_parent_folder(env):
    root, cfg, s = env
    cfg.settings.folders[0].depth = 1
    make(root, "The Seven Deadly Sins S2 [1080p]/01.mkv")
    make(root, "The Seven Deadly Sins S2 [1080p]/Episode 02.mkv")
    make(root, "The Seven Deadly Sins S2 [1080p]/S02E03.mkv")
    assert set(dests(s).values()) == {"TV Series/The Seven Deadly Sins/Season 02"}


def test_numbered_episodes_without_markers_cluster_into_a_series(env):
    root, cfg, s = env
    for n in (1, 2, 3):
        make(root, f"Seven Deadly Sins {n:02d}.mkv")
    assert set(dests(s).values()) == {"TV Series/Seven Deadly Sins/Season 01"}


def test_movie_sequels_with_years_are_not_treated_as_episodes(env):
    root, cfg, s = env
    for n, y in ((1, 1995), (2, 1999), (3, 2010)):
        make(root, f"Toy Story {n} ({y}).mkv")
    assert all(v.startswith("Movies/") for v in dests(s).values())


def test_title_alias(env):
    root, cfg, s = env
    cfg.settings.aliases = "Nanatsu no Taizai = The Seven Deadly Sins"
    make(root, "[SubsPlease] Nanatsu no Taizai - 05 (1080p).mkv")
    assert dests(s)["[SubsPlease] Nanatsu no Taizai - 05 (1080p).mkv"] == "TV Series/The Seven Deadly Sins/Season 01"


# ---- multiple folders -------------------------------------------------------
def test_multiple_folders_and_destination_base(env, tmp_path):
    root, cfg, s = env
    other = tmp_path / "desktop"
    other.mkdir()
    library = tmp_path / "library"
    cfg.settings.folders.append(d.Folder(str(other), dest_base=str(library)))
    make(root, "report.pdf")
    make(other, "setup.exe")
    moves = {os.path.basename(m.src): m for m in s.plan()}
    assert os.path.dirname(moves["report.pdf"].dest_dir).startswith(str(root))
    assert moves["setup.exe"].dest_dir == str(library / "Installers")
    s.execute(list(moves.values()))
    assert (library / "Installers" / "setup.exe").exists()
    assert not (library).parent.joinpath("desktop", "Installers").exists()


def test_disabled_folder_is_skipped(env, tmp_path):
    root, cfg, s = env
    other = tmp_path / "other"
    other.mkdir()
    cfg.settings.folders.append(d.Folder(str(other), enabled=False))
    make(other, "report.pdf")
    assert dests(s) == {}


def test_settled_files_are_rechecked_after_rules_change(env):
    root, cfg, s = env
    make(root, "weird.xyz")
    assert s.plan() == []
    cfg.rules.insert(0, d.Rule("xyz", True, "xyz", target="Strange"))
    assert dests(s)["weird.xyz"] == "Strange"


# ---- config migration -------------------------------------------------------
def test_legacy_single_root_config_is_migrated(tmp_path, monkeypatch):
    import json
    cfgfile = tmp_path / "config.json"
    cfgfile.write_text(json.dumps({"settings": {"root": str(tmp_path / "old")}, "rules": []}))
    monkeypatch.setattr(d, "CONFIG_FILE", cfgfile)
    cfg = d.load_config()
    assert [f.path for f in cfg.settings.folders] == [str(tmp_path / "old")]
    assert cfg.settings.root == ""


# ---- shared-name grouping (e.g. archives) ------------------------------------
@pytest.mark.parametrize("name,core", [
    ("MyGame.part1.rar", "MyGame"), ("MyGame (1).zip", "MyGame"), ("MyGame_v1.2.zip", "MyGame"),
    ("Photos_2023.zip", "Photos"), ("Photos-2024-03-15.zip", "Photos"), ("Report copy.zip", "Report"),
    ("backup.tar.gz", "backup"), ("Windows 10 Pro.iso", "Windows 10 Pro"),
])
def test_group_core(name, core):
    assert d.group_core(name) == core


def test_similar_archives_get_their_own_folder(env):
    root, cfg, s = env
    for n in ("MyGame.part1.rar", "MyGame.part2.rar", "MyGame (1).zip",
              "Photos_2023.zip", "Photos_2024.zip",
              "ProjectX_report.zip", "ProjectX_data.zip", "solo.zip"):
        make(root, n)
    r = dests(s)
    assert {r["MyGame.part1.rar"], r["MyGame.part2.rar"], r["MyGame (1).zip"]} == {"Archives/MyGame"}
    assert {r["Photos_2023.zip"], r["Photos_2024.zip"]} == {"Archives/Photos"}
    assert {r["ProjectX_report.zip"], r["ProjectX_data.zip"]} == {"Archives/ProjectX"}
    assert r["solo.zip"] == "Archives"


def test_short_unrelated_prefixes_do_not_group(env):
    root, cfg, s = env
    make(root, "Final Cut Pro.zip")
    make(root, "Final Fantasy.zip")
    assert set(dests(s).values()) == {"Archives"}


def test_late_archive_joins_existing_group_folder(env):
    root, cfg, s = env
    (root / "Archives" / "ProjectX").mkdir(parents=True)
    make(root, "ProjectX_logs.zip")
    make(root, "Other.zip")
    r = dests(s)
    assert r["ProjectX_logs.zip"] == "Archives/ProjectX"
    assert r["Other.zip"] == "Archives"


def test_group_placeholder_with_text_and_alias(env):
    root, cfg, s = env
    cfg.rules.insert(0, d.Rule("grp", True, "zip", target="Packs/{group} files", smart="group"))
    cfg.settings.aliases = "Photos = Picture backups"
    for n in ("Photos_2023.zip", "Photos_2024.zip", "Tools.zip", "Tools (1).zip"):
        make(root, n)
    r = dests(s)
    assert r["Photos_2023.zip"] == "Packs/Picture backups files"
    assert r["Tools.zip"] == "Packs/Tools files"


def test_explain_shows_group_name(env):
    _, _, s = env
    rule, dest = s.explain("MyGame.part1.rar")
    assert "archives" in rule.lower() and dest.endswith("Archives/MyGame/")
