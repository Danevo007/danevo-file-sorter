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
    cfg.settings.root = str(root)
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


def dests(sorter):
    return {os.path.basename(m.src): os.path.relpath(m.dest_dir, sorter.cfg.settings.root).replace("\\", "/")
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
