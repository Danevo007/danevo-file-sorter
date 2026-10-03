#!/usr/bin/env python3
"""
Danevo File Sorter
==================
Rule-based file organiser with a modern dark GUI, live watch mode,
system-tray operation and one-click undo.

Rules are checked top to bottom - the FIRST enabled rule that matches decides
where a file goes. A rule may combine (AND) any of these conditions:
    extension | content type (detected from the file's bytes) | name keywords
    regex | date in the file name | min/max size | minimum age
Destination placeholders:
    {ext} {kind} {year} {month} {month_name} {first_letter}
    {name_year} {name_month} {name_day}   (date found in the file name)
    plus any named regex group, e.g. (?P<series>...)  ->  {series}
Rule actions: move to a folder, or add to a ZIP archive.
Extras: duplicate detection (SHA-256), undo, start with Windows, tray mode.
"""

import hashlib
import json
import os
import queue
import re
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
except ImportError:  # pragma: no cover
    print("This app needs customtkinter:  pip install customtkinter")
    sys.exit(1)

try:  # optional: system tray
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except Exception:  # pragma: no cover
    HAS_TRAY = False

APP_NAME = "Danevo File Sorter"
APP_VERSION = "1.1.0"

# --------------------------------------------------------------------------
#  Paths & constants
# --------------------------------------------------------------------------
APP_DIR = Path.home() / ".danevo_file_sorter"
_OLD_DIR = Path.home() / ".downloads_sorter"
if _OLD_DIR.exists() and not APP_DIR.exists():  # migrate settings from the first version
    try:
        shutil.copytree(_OLD_DIR, APP_DIR)
    except OSError:
        pass
CONFIG_FILE = APP_DIR / "config.json"
HISTORY_FILE = APP_DIR / "history.json"
PARTIAL_EXT = {".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload"}
IGNORE_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
DUP_FOLDER = "Duplicates"
MAX_HISTORY = 30

ACCENT = "#7c6cf0"
ACCENT_HOVER = "#6a5ae0"
BG = "#12141c"
SIDEBAR = "#0d0f15"
CARD = "#1b1f2b"
CARD_OFF = "#151821"
MUTED = "#8b93a7"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


# --------------------------------------------------------------------------
#  Content-type detection & filename dates
# --------------------------------------------------------------------------
KIND_EXT = {
    "image": "jpg jpeg png gif webp svg heic bmp tiff ico raw avif".split(),
    "video": "mp4 mkv avi mov wmv webm flv m4v mpg mpeg 3gp".split(),
    "audio": "mp3 wav flac m4a aac ogg opus wma".split(),
    "document": "pdf doc docx xls xlsx ppt pptx txt odt ods rtf csv epub md".split(),
    "subtitle": "srt sub ass ssa vtt idx sup".split(),
    "archive": "zip rar 7z tar gz bz2 xz tgz".split(),
    "executable": "exe msi dmg pkg deb rpm apk appimage bat sh".split(),
    "code": "py js ts json html css sql ipynb java c cpp cs go rs php".split(),
}
EXT_TO_KIND = {e: k for k, exts in KIND_EXT.items() for e in exts}
MAGIC = [(b"\x89PNG", "image"), (b"\xff\xd8\xff", "image"), (b"GIF8", "image"),
         (b"%PDF", "document"), (b"PK\x03\x04", "archive"), (b"Rar!", "archive"),
         (b"7z\xbc\xaf", "archive"), (b"\x1f\x8b", "archive"), (b"MZ", "executable"),
         (b"ID3", "audio"), (b"OggS", "audio"), (b"fLaC", "audio"),
         (b"\x1a\x45\xdf\xa3", "video")]


def detect_kind(path: str, ext: str) -> str:
    """Kind from the extension; when unknown, sniff the file's first bytes."""
    if ext in EXT_TO_KIND:
        return EXT_TO_KIND[ext]
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return "other"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1"):
            return "image"
        return "audio" if brand == b"M4A " else "video"
    if head[:4] == b"RIFF":
        return {b"WAVE": "audio", b"AVI ": "video", b"WEBP": "image"}.get(head[8:12], "other")
    for sig, kind in MAGIC:
        if head.startswith(sig):
            return kind
    return "other"


_DATE_YMD = re.compile(r"(?<!\d)(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_DATE_DMY = re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[01])[-_.](0[1-9]|1[0-2])[-_.](20\d{2})(?!\d)")


def date_in_name(name: str):
    """Return (year, month, day) strings found in a file name, else None."""
    m = _DATE_YMD.search(name)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = _DATE_DMY.search(name)
    if m:
        return m.group(3), m.group(2), m.group(1)
    return None


# --------------------------------------------------------------------------
#  Smart name parsing (movies, series, subtitles)
# --------------------------------------------------------------------------
SUB_EXT = set(KIND_EXT["subtitle"])
LANG_TOKENS = {"en", "eng", "english", "sw", "swa", "swahili", "fr", "fre", "french", "es", "spa",
               "spanish", "ar", "ara", "arabic", "de", "ger", "german", "pt", "por", "it", "ita",
               "hi", "hin", "forced", "sdh", "cc", "default", "subs", "sub", "subtitle", "subtitles"}
_QUALITY = re.compile(
    r"\b(?:480p|576p|720p|1080p|1440p|2160p|4k|uhd|hdr10?|bluray|blu ray|bdrip|brrip|webrip|web dl|"
    r"webdl|hdrip|dvdrip|dvdscr|hdtv|hdcam|x264|x265|h 264|h 265|h264|h265|hevc|xvid|divx|aac\d*|"
    r"ac3|dts|ddp\d*|atmos|10bit|remux|repack|extended|unrated|imax|dual audio)\b", re.I)
_YEAR = re.compile(r"(?<!\d)(19[2-9]\d|20[0-4]\d)(?!\d)")
_EPISODE = [
    (re.compile(r"\bS(\d{1,2})\s?E(\d{1,3})\b", re.I), 1, 2),
    (re.compile(r"\b(\d{1,2})x(\d{2,3})\b", re.I), 1, 2),
    (re.compile(r"\bSeason\s?(\d{1,2})(?:\s?Episode\s?(\d{1,3}))?\b", re.I), 1, 2),
    (re.compile(r"\bS(\d{1,2})\b(?!\s?E\d)", re.I), 1, None),
]


def parse_media(fname: str):
    """Pull title / year / season / episode out of a release-style file name.
    'Breaking.Bad.S02E05.720p.WEB-DL.mkv' -> title 'Breaking Bad', season 2, episode 5
    '[YTS.MX] Inception (2010) [1080p].mp4' -> title 'Inception', year 2010
    Subtitle language tails ('.en', '.eng.forced') are ignored so subs match their video."""
    stem, ext = os.path.splitext(fname)
    ext = ext.lower().lstrip(".")
    s = re.sub(r"^(?:\[[^\]]*\]\s*)+", "", stem)  # leading [release group] tags
    s = re.sub(r"^(?:www\.)?[\w-]+\.(?:com|net|org|to|cc|me|io|tv|ws|co)[\s._-]+", "", s, flags=re.I)
    s = re.sub(r"[._]+", " ", s)
    if ext in SUB_EXT:
        toks = s.split()
        while len(toks) > 1 and re.sub(r"[^a-z]", "", toks[-1].lower()) in LANG_TOKENS:
            toks.pop()
        s = " ".join(toks)

    def _brackets(m):  # keep a bare year, drop other [tags] / (tags)
        inner = m.group(0)[1:-1].strip()
        return f" {inner} " if _YEAR.fullmatch(inner) else " "
    s = re.sub(r"\s+", " ", re.sub(r"[\[(][^\])]*[\])]", _brackets, s)).strip()

    season = episode = None
    is_series, cuts = False, []
    for rx, gs, ge in _EPISODE:
        m = rx.search(s)
        if m:
            is_series, season = True, int(m.group(gs))
            episode = int(m.group(ge)) if ge and m.group(ge) else None
            cuts.append(m.start())
            break
    qm = _QUALITY.search(s)
    if qm:
        cuts.append(qm.start())
    limit = min(cuts) if cuts else len(s)
    years = [m for m in _YEAR.finditer(s) if 0 < m.start() < limit]
    year = None
    if years:
        year = int(years[-1].group(1))
        cuts.append(years[-1].start())
    title = s[:min(cuts)] if cuts else s
    title = re.sub(r"\s+", " ", title.strip(" -\u2013\u2014:,"))
    if not title:
        return None
    if title.islower() or title.isupper():
        title = string.capwords(title)
    return {"title": title, "year": year, "season": season, "episode": episode,
            "series": is_series, "movie": (not is_series) and (year is not None or qm is not None)}


def media_placeholders(info: dict) -> dict:
    t, y, se, ep = info["title"], info["year"], info["season"], info["episode"]
    return {
        "title": t,
        "title_year": str(y) if y else "",
        "movie": f"{t} ({y})" if y else t,
        "season": f"Season {se:02d}" if se is not None else "",
        "episode": f"S{se:02d}E{ep:02d}" if se is not None and ep is not None else "",
    }


def norm_key(name: str) -> str:
    """Comparison key so 'Breaking Bad', 'breaking.bad' and 'Season 02' / 'season 2' unify."""
    k = re.sub(r"\b0+(?=\d)", "", name.casefold())
    return re.sub(r"[^0-9a-z]+", "", k)


# --------------------------------------------------------------------------
#  Data model
# --------------------------------------------------------------------------
def clean_value(v: str) -> str:
    v = re.sub(r"[._]+", " ", v)
    v = re.sub(r"\s+", " ", v).strip(" -_.")
    return v


def safe_part(p: str) -> str:
    p = re.sub(r'[<>:"|?*\x00-\x1f]', "", p).strip().rstrip(".")
    return "" if p in (".", "..") else p


class SafeDict(dict):
    def __missing__(self, key):
        return "Unknown"


@dataclass
class Rule:
    name: str = "New rule"
    enabled: bool = True
    extensions: str = ""
    keywords: str = ""
    regex: str = ""
    min_mb: float = 0
    max_mb: float = 0
    older_days: float = 0
    target: str = ""
    kind: str = ""            # "image, video" (detected by content when needed)
    name_date: bool = False   # file name must contain a date
    action: str = "move"      # "move" | "zip"
    smart: str = ""           # name pattern: "" | "series" | "movie" | "title"

    def has_conditions(self) -> bool:
        return any([self.extensions.strip(), self.keywords.strip(), self.regex.strip(),
                    self.kind.strip(), self.name_date, self.smart, self.min_mb, self.max_mb, self.older_days])

    def summary(self) -> str:
        bits = []
        if self.extensions.strip():
            bits.append("ext: " + self.extensions.strip())
        if self.kind.strip():
            bits.append("type: " + self.kind.strip())
        if self.smart:
            bits.append({"series": "name: TV episode", "movie": "name: movie",
                         "title": "name: group by title"}.get(self.smart, "name pattern"))
        if self.keywords.strip():
            bits.append("name has: " + self.keywords.strip())
        if self.regex.strip():
            bits.append("regex")
        if self.name_date:
            bits.append("date in name")
        if self.min_mb:
            bits.append(f"≥ {self.min_mb:g} MB")
        if self.max_mb:
            bits.append(f"≤ {self.max_mb:g} MB")
        if self.older_days:
            bits.append(f"older than {self.older_days:g} d")
        return "  ·  ".join(bits) or "no conditions"

    def match(self, fname: str, size: int, mtime: float, path: str = ""):
        """Return dict of captured groups if the file matches, else None."""
        if not self.has_conditions():
            return None
        ext = os.path.splitext(fname)[1].lower().lstrip(".")
        exts = [e.strip().lower().lstrip(".") for e in self.extensions.split(",") if e.strip()]
        if exts and ext not in exts:
            return None
        kws = [k.strip().lower() for k in self.keywords.split(",") if k.strip()]
        if kws and not any(k in fname.lower() for k in kws):
            return None
        if self.name_date and date_in_name(fname) is None:
            return None
        groups = {}
        if self.smart:
            info = parse_media(fname)
            if info is None:
                return None
            if self.smart == "series" and not info["series"]:
                return None
            if self.smart == "movie" and not info["movie"]:
                return None
            groups.update(media_placeholders(info))
        if self.regex.strip():
            try:
                m = re.search(self.regex, fname, re.IGNORECASE)
            except re.error:
                return None
            if not m:
                return None
            groups.update({k: clean_value(v) for k, v in m.groupdict().items() if v})
        mb = size / 1048576
        if self.min_mb and mb < self.min_mb:
            return None
        if self.max_mb and mb > self.max_mb:
            return None
        if self.older_days and (time.time() - mtime) < self.older_days * 86400:
            return None
        kinds = [k.strip().lower() for k in self.kind.split(",") if k.strip()]
        if kinds and detect_kind(path or fname, ext) not in kinds:  # last: may read the file
            return None
        return groups


@dataclass
class Settings:
    root: str = str(Path.home() / "Downloads")
    settle_seconds: float = 8
    poll_seconds: float = 4
    unmatched_folder: str = ""
    watch_on_start: bool = False
    duplicate_mode: str = "off"     # "off" | "move"
    keep_in_tray: bool = True
    start_with_windows: bool = False


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    rules: list = field(default_factory=list)


def default_rules():
    VID = "mp4, mkv, avi, mov, wmv, webm"
    return [
        Rule("TV series (any episode name)", True, "", "", "", 0, 0, 0, "TV Series/{title}/{season}",
             kind="video, subtitle", smart="series"),
        Rule("Movies (name with year / quality tags)", True, "", "", "", 0, 0, 0, "Movies/{movie}",
             kind="video, subtitle", smart="movie"),
        Rule("Loose subtitles -> matching movie folder", True, "", "", "", 0, 0, 0, "Movies/{movie}",
             kind="subtitle", smart="title"),
        Rule("Installers", True, "exe, msi, dmg, pkg, deb, rpm, apk", "", "", 0, 0, 0, "Installers"),
        Rule("Disk images", True, "iso, img", "", "", 0, 0, 0, "Disk Images"),
        Rule("Photos dated in name", True, "", "", "", 0, 0, 0, "Images/{name_year}/{name_month}",
             kind="image", name_date=True),
        Rule("Documents", True, "pdf, doc, docx, xls, xlsx, ppt, pptx, txt, odt, csv, epub", "", "",
             0, 0, 0, "Documents/{year}"),
        Rule("Images", True, "jpg, jpeg, png, gif, webp, svg, heic, bmp, raw", "", "", 0, 0, 0,
             "Images/{year}"),
        Rule("Videos", True, VID, "", "", 0, 0, 0, "Videos"),
        Rule("Audio", True, "mp3, wav, flac, m4a, aac, ogg", "", "", 0, 0, 0, "Audio"),
        Rule("Archives", True, "zip, rar, 7z, tar, gz, bz2", "", "", 0, 0, 0, "Archives"),
        Rule("Code & data", True, "py, js, ts, json, html, css, sql, ipynb", "", "", 0, 0, 0, "Code"),
        Rule("By content (odd or missing extensions)", True, "", "", "", 0, 0, 0, "By Content/{kind}",
             kind="image, video, audio, document, archive, executable"),
        Rule("Huge files (> 2 GB)", False, "", "", "", 2048, 0, 0, "Large Files"),
        Rule("ZIP away anything older than 90 days", False, "", "", "", 0, 0, 90, "Old Files/{year}",
             action="zip"),
    ]


def load_config() -> Config:
    cfg = Config(rules=default_rules())
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        s_names = {f.name for f in fields(Settings)}
        r_names = {f.name for f in fields(Rule)}
        cfg.settings = Settings(**{k: v for k, v in raw.get("settings", {}).items() if k in s_names})
        rules = [Rule(**{k: v for k, v in r.items() if k in r_names}) for r in raw.get("rules", [])]
        if rules:
            cfg.rules = rules
    except (OSError, ValueError, TypeError):
        pass
    return cfg


def save_config(cfg: Config):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = {"settings": asdict(cfg.settings), "rules": [asdict(r) for r in cfg.rules]}
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
#  Sorting engine
# --------------------------------------------------------------------------
@dataclass
class Move:
    src: str
    dest_dir: str
    rule: str
    action: str = "move"     # "move" | "zip"
    zip_name: str = ""


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    i = 1
    while True:
        cand = p.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


class Sorter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.RLock()
        self._hash_cache = {}
        self._index = {}
        self._index_t = 0.0
        self._index_root = ""
        self._claimed = {}

    # -- destination -------------------------------------------------------
    def _dest_dir(self, root: Path, rule: Rule, path: str, fname: str, mtime: float, groups: dict) -> Path:
        ext = os.path.splitext(fname)[1].lstrip(".")
        dt = datetime.fromtimestamp(mtime)
        nd = date_in_name(fname) or (str(dt.year), f"{dt.month:02d}", f"{dt.day:02d}")
        ctx = SafeDict(
            ext=clean_value(ext).upper() or "No Extension",
            year=dt.year, month=f"{dt.month:02d}", month_name=dt.strftime("%B"),
            first_letter=(fname[:1].upper() if fname[:1].isalnum() else "#"),
            name_year=nd[0], name_month=nd[1], name_day=nd[2],
        )
        if "{kind}" in rule.target:
            ctx["kind"] = detect_kind(path, ext.lower()).capitalize()
        ctx.update(groups)
        try:
            text = rule.target.format_map(ctx)
        except (ValueError, IndexError, KeyError):
            text = rule.target
        text = text.strip()
        if Path(text).is_absolute():
            return Path(text)
        parts = [safe_part(p) for p in re.split(r"[\\/]+", text)]
        parts = [p for p in parts if p]
        cur = root
        for p in parts:  # reuse existing folders that differ only in case/punctuation/year
            cur = cur / self._reuse(cur, p)
        return cur

    def _reuse(self, parent: Path, part: str) -> str:
        key = norm_key(part)
        if not key:
            return part
        claimed = self._claimed.get((str(parent), key))
        if claimed:
            return claimed
        found = None
        has_year = bool(re.search(r"(19|20)\d\d$", key))
        try:
            with os.scandir(parent) as it:
                for e in it:
                    if not e.is_dir():
                        continue
                    k = norm_key(e.name)
                    if k == key or (not has_year and k != key and re.sub(r"(19|20)\d\d$", "", k) == key):
                        found = e.name
                        break
        except OSError:
            pass
        self._claimed[(str(parent), key)] = found or part
        return found or part

    # -- duplicates ---------------------------------------------------------
    def _hash(self, path: str):
        try:
            st = os.stat(path)
            key = (path, st.st_size, st.st_mtime_ns)
            if key in self._hash_cache:
                return self._hash_cache[key]
            sha = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    sha.update(chunk)
        except OSError:
            return None
        self._hash_cache[key] = sha.hexdigest()
        return self._hash_cache[key]

    def _size_index(self, root: Path) -> dict:
        if self._index_root == str(root) and time.time() - self._index_t < 60:
            return self._index
        idx = {}
        for dp, dn, fn in os.walk(root):
            if Path(dp) == root and DUP_FOLDER in dn:
                dn.remove(DUP_FOLDER)
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if size > 0:
                    idx.setdefault(size, []).append(p)
        self._index, self._index_t, self._index_root = idx, time.time(), str(root)
        return idx

    def _find_duplicates(self, root: Path, cands) -> set:
        if not cands:
            return set()
        idx = self._size_index(root)
        cset = {c[0] for c in cands}
        dups, kept = set(), {}
        for path, _name, info in sorted(cands, key=lambda c: c[2].st_mtime):  # oldest is the original
            size = info.st_size
            if size == 0:
                continue
            refs = [p for p in idx.get(size, []) if p != path and p not in cset and os.path.exists(p)]
            refs += kept.get(size, [])
            if refs:
                h = self._hash(path)
                if h and any(self._hash(r) == h for r in refs):
                    dups.add(path)
                    continue
            kept.setdefault(size, []).append(path)
        return dups

    # -- planning ----------------------------------------------------------
    def plan(self) -> list:
        with self.lock:
            st = self.cfg.settings
            root = Path(st.root)
            out = []
            if not root.is_dir():
                return out
            rules = [r for r in self.cfg.rules if r.enabled and r.target.strip()]
            now = time.time()
            self._claimed = {}
            cands = []
            with os.scandir(root) as it:
                for e in it:
                    try:
                        if not e.is_file(follow_symlinks=False):
                            continue
                        name = e.name
                        low = name.lower()
                        if (name.startswith((".", "~$")) or low in IGNORE_NAMES
                                or os.path.splitext(low)[1] in PARTIAL_EXT):
                            continue
                        info = e.stat()
                    except OSError:
                        continue
                    if now - info.st_mtime < st.settle_seconds:
                        continue  # probably still being written
                    cands.append((e.path, name, info))

            dups = self._find_duplicates(root, cands) if st.duplicate_mode == "move" else set()
            for path, name, info in cands:
                if path in dups:
                    out.append(Move(path, str(root / DUP_FOLDER), "(duplicate)"))
                    continue
                dest, rule_name, action = None, "", "move"
                for r in rules:
                    g = r.match(name, info.st_size, info.st_mtime, path)
                    if g is not None:
                        dest = self._dest_dir(root, r, path, name, info.st_mtime, g)
                        rule_name, action = r.name, r.action
                        break
                if dest is None and st.unmatched_folder.strip():
                    parts = [safe_part(p) for p in re.split(r"[\\/]+", st.unmatched_folder)]
                    dest = root.joinpath(*[p for p in parts if p])
                    rule_name = "(unmatched)"
                if dest is None or dest == root:
                    continue
                if action == "zip":
                    if dest.suffix.lower() != ".zip":
                        dest = dest.with_name(dest.name + ".zip")
                    out.append(Move(path, str(dest.parent), rule_name, "zip", dest.name))
                else:
                    out.append(Move(path, str(dest), rule_name))
            out.sort(key=lambda m: m.src.lower())
            return out

    def explain(self, fname: str):
        """What would happen to a file with this name? -> (rule name, relative destination) or None."""
        with self.lock:
            root, now, self._claimed = Path(self.cfg.settings.root), time.time(), {}
            for r in self.cfg.rules:
                if not (r.enabled and r.target.strip()):
                    continue
                g = r.match(fname, 50 * 1048576, now, fname)
                if g is not None:
                    dest = self._dest_dir(root, r, fname, fname, now, g)
                    if r.action == "zip":
                        return r.name, f"{self._rel(str(dest))}.zip (ZIP)"
                    return r.name, self._rel(str(dest)) + "/"
            return None

    # -- execution ---------------------------------------------------------
    def _rel(self, d: str) -> str:
        try:
            return str(Path(d).relative_to(self.cfg.settings.root))
        except ValueError:
            return d

    def execute(self, moves, progress=None, log=None):
        done, failed = [], 0
        with self.lock:
            total = max(len(moves), 1)
            for i, m in enumerate(moves, 1):
                src = Path(m.src)
                try:
                    if not src.exists():
                        continue
                    Path(m.dest_dir).mkdir(parents=True, exist_ok=True)
                    if m.action == "zip":
                        zp = Path(m.dest_dir) / m.zip_name
                        mtime, size = src.stat().st_mtime, src.stat().st_size
                        with zipfile.ZipFile(zp, "a", zipfile.ZIP_DEFLATED) as z:
                            names, arc, n = set(z.namelist()), src.name, 1
                            while arc in names:
                                arc, n = f"{src.stem} ({n}){src.suffix}", n + 1
                            z.write(src, arc)
                            if z.getinfo(arc).file_size != size:
                                raise OSError("zip verification failed")
                        src.unlink()
                        done.append({"src": str(src), "dst": str(zp), "entry": arc, "mtime": mtime})
                        if log:
                            log(f"▣ {src.name}  →  zipped into {self._rel(str(zp))}   [{m.rule}]")
                    else:
                        dst = unique_path(Path(m.dest_dir) / src.name)
                        shutil.move(str(src), str(dst))
                        done.append({"src": str(src), "dst": str(dst)})
                        if log:
                            log(f"✓ {src.name}  →  {self._rel(m.dest_dir)}   [{m.rule}]")
                except Exception as exc:
                    failed += 1
                    if log:
                        log(f"✗ {src.name}: {exc}")
                if progress:
                    progress(i / total)
            if done:
                self._push_history(done)
                self._index_t = 0  # force a fresh duplicate index next time
        return len(done), failed

    # -- history / undo ----------------------------------------------------
    def _read_history(self):
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _push_history(self, batch):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        hist = self._read_history()
        hist.append({"time": datetime.now().isoformat(timespec="seconds"), "moves": batch})
        HISTORY_FILE.write_text(json.dumps(hist[-MAX_HISTORY:]), encoding="utf-8")

    def _prune(self, d: Path):
        root = Path(self.cfg.settings.root)
        while d != root and root in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent

    def _unzip_entry(self, zp: Path, entry: str, dest: Path, mtime):
        with zipfile.ZipFile(zp) as z:
            with z.open(entry) as a, open(dest, "wb") as b:
                shutil.copyfileobj(a, b)
        if mtime:
            os.utime(dest, (mtime, mtime))
        tmp, left = zp.with_name(zp.name + ".tmp"), 0
        with zipfile.ZipFile(zp) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == entry:
                    continue
                with zin.open(item) as s, zout.open(item, "w", force_zip64=item.file_size > 2 ** 31) as d:
                    shutil.copyfileobj(s, d)
                left += 1
        if left:
            os.replace(tmp, zp)
        else:
            tmp.unlink()
            zp.unlink()
            self._prune(zp.parent)

    def undo_last(self, log=None):
        with self.lock:
            hist = self._read_history()
            if not hist:
                return 0
            batch = hist.pop()
            restored = 0
            for mv in reversed(batch["moves"]):
                src, dst = Path(mv["src"]), Path(mv["dst"])
                try:
                    if not dst.exists():
                        continue
                    src.parent.mkdir(parents=True, exist_ok=True)
                    if "entry" in mv:
                        self._unzip_entry(dst, mv["entry"], unique_path(src), mv.get("mtime"))
                    else:
                        shutil.move(str(dst), str(unique_path(src)))
                        self._prune(dst.parent)
                    restored += 1
                    if log:
                        log(f"↩ {src.name} restored")
                except Exception as exc:
                    if log:
                        log(f"✗ undo {src.name}: {exc}")
            HISTORY_FILE.write_text(json.dumps(hist), encoding="utf-8")
            self._index_t = 0
            return restored


# --------------------------------------------------------------------------
#  OS helpers
# --------------------------------------------------------------------------
def open_folder(path: str):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def set_startup(enabled: bool) -> bool:
    """Add/remove the app from the current user's Windows startup list."""
    if not sys.platform.startswith("win"):
        return False
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    try:
        if enabled:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" --minimized'
            else:
                py = sys.executable.replace("python.exe", "pythonw.exe")
                cmd = f'"{py}" "{Path(__file__).resolve()}" --minimized'
            winreg.SetValueEx(key, "DanevoFileSorter", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "DanevoFileSorter")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)
    return True


def tray_image():
    for name in ("danevo.png",):
        try:
            return Image.open(resource_path(name)).convert("RGBA")
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))  # fallback glyph
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 63, 63), radius=14, fill=(124, 108, 240, 255))
    d.rounded_rectangle((14, 22, 50, 48), radius=5, fill=(255, 255, 255, 255))
    return img


# --------------------------------------------------------------------------
#  GUI
# --------------------------------------------------------------------------
class RuleDialog(ctk.CTkToplevel):
    FIELDS = [
        ("name", "Rule name", "e.g. TV series + subtitles"),
        ("extensions", "Extensions (comma separated)", "mp4, mkv, srt, ass"),
        ("kind", "Content type (any of) - detected from the file itself",
         "image, video, audio, document, archive, executable, code"),
        ("keywords", "Name contains (any of)", "invoice, receipt"),
        ("regex", "Name regex (optional)", r"^(?P<series>.+?)[. _-]+S\d+E\d+"),
        ("min_mb", "Minimum size (MB)", "0 = no limit"),
        ("max_mb", "Maximum size (MB)", "0 = no limit"),
        ("older_days", "Older than (days)", "0 = ignore age"),
        ("target", "Destination (folder, or ZIP name for ZIP action)", "TV Series/{series}"),
    ]
    ACTIONS = ("Move to folder", "Add to ZIP archive")
    SMARTS = (("Off", ""),
              ("TV episode  (S01E02, 1x02, Season 1…)", "series"),
              ("Movie  (has a year or quality tags)", "movie"),
              ("Any title  (group by cleaned name)", "title"))

    def __init__(self, parent, rule: Rule, on_save):
        super().__init__(parent)
        self.title("Edit rule")
        self.geometry("620x900")
        self.configure(fg_color=BG)
        self.transient(parent)
        self.after(80, self.grab_set)
        self.on_save, self.rule, self.entries = on_save, rule, {}

        ctk.CTkLabel(self, text="Rule editor", font=ctk.CTkFont(size=22, weight="bold")).pack(
            anchor="w", padx=28, pady=(24, 4))
        ctk.CTkLabel(self, text="All filled-in conditions must match (AND).", text_color=MUTED).pack(
            anchor="w", padx=28, pady=(0, 10))
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16)
        for key, label, ph in self.FIELDS:
            ctk.CTkLabel(body, text=label, text_color=MUTED, font=ctk.CTkFont(size=12)).pack(
                anchor="w", padx=12, pady=(10, 2))
            e = ctk.CTkEntry(body, placeholder_text=ph, height=38, corner_radius=10,
                             fg_color=CARD, border_width=0)
            e.pack(fill="x", padx=12)
            val = getattr(rule, key)
            if val not in ("", 0, 0.0):
                e.insert(0, str(val))
            self.entries[key] = e

        ctk.CTkLabel(body, text="Smart name pattern - groups related files by the title in their name",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(anchor="w", padx=12, pady=(16, 2))
        cur = next((lab for lab, code in self.SMARTS if code == rule.smart), self.SMARTS[0][0])
        self.smart_var = ctk.StringVar(value=cur)
        ctk.CTkOptionMenu(body, values=[lab for lab, _ in self.SMARTS], variable=self.smart_var,
                          fg_color=CARD, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                          height=36, width=380, anchor="w").pack(anchor="w", padx=12)

        self.date_sw = ctk.CTkSwitch(body, text="File name must contain a date  (2024-03-15 · 20240315 · 15.03.2024)",
                                     progress_color=ACCENT)
        self.date_sw.pack(anchor="w", padx=12, pady=(16, 4))
        if rule.name_date:
            self.date_sw.select()
        ctk.CTkLabel(body, text="Action", text_color=MUTED, font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=12, pady=(12, 2))
        self.action_var = ctk.StringVar(value=self.ACTIONS[1] if rule.action == "zip" else self.ACTIONS[0])
        ctk.CTkOptionMenu(body, values=list(self.ACTIONS), variable=self.action_var, fg_color=CARD,
                          button_color=ACCENT, button_hover_color=ACCENT_HOVER, height=36).pack(
            anchor="w", padx=12)

        hint = ("Smart name placeholders: {title} {movie} {title_year} {season} {episode}\n"
                "e.g. TV Series/{title}/{season}  or  Movies/{movie}. Videos and their subtitles\n"
                "share a title, so they land together whatever their extension.\n\n"
                "Placeholders: {ext} {kind} {year} {month} {month_name} {first_letter}\n"
                "{name_year} {name_month} {name_day} (date in the file name) and any regex named\n"
                "group like (?P<series>…) → {series}. Use / for sub-folders.\n"
                "ZIP action: destination 'Archive/{year}' creates Archive/<year>.zip inside the folder.")
        ctk.CTkLabel(body, text=hint, text_color=MUTED, justify="left", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=12, pady=(16, 6))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=28, pady=18)
        ctk.CTkButton(row, text="Cancel", width=100, height=38, fg_color=CARD, hover_color="#262b3a",
                      command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text="Save rule", width=120, height=38, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self.save).pack(side="right")

    def save(self):
        v = {k: e.get().strip() for k, e in self.entries.items()}
        try:
            for k in ("min_mb", "max_mb", "older_days"):
                v[k] = float(v[k] or 0)
            if v["regex"]:
                re.compile(v["regex"])
        except (ValueError, re.error) as exc:
            messagebox.showerror("Invalid value", str(exc), parent=self)
            return
        bad = [k for k in v["kind"].replace(" ", "").lower().split(",") if k and k not in KIND_EXT]
        if bad:
            messagebox.showerror("Unknown content type",
                                 f"Unknown: {', '.join(bad)}\nUse: {', '.join(KIND_EXT)}", parent=self)
            return
        if not v["name"] or not v["target"]:
            messagebox.showerror("Missing info", "Name and destination are required.", parent=self)
            return
        action = "zip" if self.action_var.get() == self.ACTIONS[1] else "move"
        smart = dict(self.SMARTS)[self.smart_var.get()]
        new = Rule(enabled=self.rule.enabled, name_date=bool(self.date_sw.get()), action=action,
                   smart=smart, **v)
        if not new.has_conditions():
            messagebox.showerror("Missing info", "Add at least one condition.", parent=self)
            return
        self.on_save(new)
        self.destroy()


class NameTester(ctk.CTkToplevel):
    """Type a file name, see how it is parsed and where your rules would send it."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Test a file name")
        self.geometry("640x400")
        self.configure(fg_color=BG)
        self.transient(app)
        ctk.CTkLabel(self, text="Name tester", font=ctk.CTkFont(size=22, weight="bold")).pack(
            anchor="w", padx=28, pady=(24, 2))
        ctk.CTkLabel(self, text="Paste any file name - nothing is moved.", text_color=MUTED).pack(
            anchor="w", padx=28)
        self.entry = ctk.CTkEntry(self, height=42, corner_radius=10, fg_color=CARD, border_width=0,
                                  placeholder_text="Breaking.Bad.S02E05.720p.WEB-DL.mkv")
        self.entry.pack(fill="x", padx=28, pady=16)
        self.entry.bind("<KeyRelease>", lambda e: self.update_result())
        self.out = ctk.CTkLabel(self, text="", justify="left", anchor="nw", wraplength=570,
                                font=ctk.CTkFont(size=14))
        self.out.pack(fill="both", expand=True, padx=28)
        self.after(100, self.entry.focus_set)

    def update_result(self):
        name = self.entry.get().strip()
        if not name:
            self.out.configure(text="")
            return
        info = parse_media(name)
        lines = []
        if info:
            kind = "TV episode" if info["series"] else ("movie" if info["movie"] else "plain title")
            ph = media_placeholders(info)
            bits = [f"title: {info['title']}", f"looks like: {kind}"]
            if info["year"]:
                bits.append(f"year: {info['year']}")
            if ph["season"]:
                bits.append(ph["season"])
            if ph["episode"]:
                bits.append(ph["episode"])
            lines.append("Detected   " + "   ·   ".join(bits))
        else:
            lines.append("Detected   no usable title")
        res = self.app.sorter.explain(name)
        if res:
            lines.append(f"\nRule           {res[0]}\nGoes to       {res[1]}")
        else:
            lines.append("\nNo rule matches - the file would stay where it is.")
        self.out.configure(text="\n".join(lines))


class App(ctk.CTk):
    def __init__(self, minimized=False):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title(APP_NAME)
        self.geometry("1140x760")
        self.minsize(980, 640)
        self.configure(fg_color=BG)
        self._set_window_icon()

        self.cfg = load_config()
        self.sorter = Sorter(self.cfg)
        self.events = queue.Queue()
        self.pending, self.busy, self.tray = [], False, None
        self.stop_evt = threading.Event()
        self.watcher = None
        self._cards, self._drag_i = [], None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=28, pady=24)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {"Rules": self._build_rules_page(), "Preview": self._build_preview_page(),
                      "Activity": self._build_activity_page(), "Settings": self._build_settings_page()}
        self.show("Rules")
        self.refresh_rules()
        self.after(150, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

        if self.cfg.settings.watch_on_start or minimized:
            self.watch_switch.select()
            self.toggle_watch()
        if minimized and HAS_TRAY:
            self.after(300, self._hide_to_tray)

    def _set_window_icon(self):
        ico = resource_path("danevo.ico")
        if sys.platform.startswith("win") and ico.exists():
            try:  # CTk applies its own icon after ~200ms; override it afterwards
                self.after(300, lambda: self.iconbitmap(str(ico)))
            except Exception:
                pass

    # ---- layout ---------------------------------------------------------
    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=SIDEBAR)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        ctk.CTkLabel(sb, text="◢ Danevo", font=ctk.CTkFont(size=28, weight="bold"),
                     text_color=ACCENT).pack(anchor="w", padx=24, pady=(30, 0))
        ctk.CTkLabel(sb, text="FILE SORTER", text_color=MUTED, font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=28, pady=(0, 28))
        self.nav = {}
        for name, icon in (("Rules", "☰"), ("Preview", "◉"), ("Activity", "≋"), ("Settings", "⚙")):
            b = ctk.CTkButton(sb, text=f"  {icon}   {name}", anchor="w", height=44, corner_radius=12,
                              fg_color="transparent", hover_color="#1a1e2b", text_color="#cfd4e4",
                              font=ctk.CTkFont(size=15), command=lambda n=name: self.show(n))
            b.pack(fill="x", padx=14, pady=3)
            self.nav[name] = b
        bottom = ctk.CTkFrame(sb, fg_color=CARD, corner_radius=14)
        bottom.pack(side="bottom", fill="x", padx=14, pady=20)
        self.status_dot = ctk.CTkLabel(bottom, text="● Watching is off", text_color=MUTED,
                                       font=ctk.CTkFont(size=12))
        self.status_dot.pack(anchor="w", padx=14, pady=(14, 6))
        self.watch_switch = ctk.CTkSwitch(bottom, text="Auto-sort new files", progress_color=ACCENT,
                                          command=self.toggle_watch)
        self.watch_switch.pack(anchor="w", padx=14, pady=(0, 14))

    def _page_header(self, parent, title, subtitle):
        h = ctk.CTkFrame(parent, fg_color="transparent")
        h.pack(fill="x")
        left = ctk.CTkFrame(h, fg_color="transparent")
        left.pack(side="left")
        ctk.CTkLabel(left, text=title, font=ctk.CTkFont(size=30, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(left, text=subtitle, text_color=MUTED).pack(anchor="w", pady=(2, 0))
        right = ctk.CTkFrame(h, fg_color="transparent")
        right.pack(side="right")
        return right

    def _btn(self, parent, text, cmd, primary=False, **kw):
        return ctk.CTkButton(parent, text=text, command=cmd, height=40, corner_radius=12,
                             fg_color=ACCENT if primary else CARD,
                             hover_color=ACCENT_HOVER if primary else "#262b3a", **kw)

    def show(self, name):
        for p in self.pages.values():
            p.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        for n, b in self.nav.items():
            b.configure(fg_color="#1d2132" if n == name else "transparent")
        if name == "Preview":
            self.scan()

    # ---- Rules page -----------------------------------------------------
    def _build_rules_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        right = self._page_header(page, "Sorting rules",
                                  "Top rule wins. Drag a rule's number or use ▲ ▼ to set its priority.")
        self._btn(right, "Reset defaults", self.reset_rules).pack(side="right", padx=(8, 0))
        self._btn(right, "Test a name", lambda: NameTester(self)).pack(side="right", padx=(8, 0))
        self._btn(right, "＋  New rule", self.new_rule, primary=True).pack(side="right")
        self.rule_list = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.rule_list.pack(fill="both", expand=True, pady=(18, 0))
        return page

    def refresh_rules(self):
        for w in self.rule_list.winfo_children():
            w.destroy()
        self._cards = []
        n = len(self.cfg.rules)
        for i, r in enumerate(self.cfg.rules):
            card = ctk.CTkFrame(self.rule_list, corner_radius=14, fg_color=CARD if r.enabled else CARD_OFF,
                                border_width=0, border_color=ACCENT)
            card.pack(fill="x", pady=5, padx=2)
            card.grid_columnconfigure(1, weight=1)
            self._cards.append(card)
            badge = ctk.CTkLabel(card, text=f"{i + 1}", width=44, height=44, corner_radius=12,
                                 fg_color=ACCENT if r.enabled else "#2a2f40",
                                 font=ctk.CTkFont(size=16, weight="bold"))
            badge.grid(row=0, column=0, rowspan=2, padx=14, pady=14)
            badge.bind("<ButtonPress-1>", lambda e, idx=i: self._drag_start(idx))
            badge.bind("<ButtonRelease-1>", lambda e, idx=i: self._drag_end(e, idx))
            colr = "#ffffff" if r.enabled else MUTED
            tag = "  ▣ ZIP" if r.action == "zip" else ""
            ctk.CTkLabel(card, text=r.name + tag, anchor="w", text_color=colr,
                         font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=1, sticky="ew",
                                                                         pady=(12, 0))
            ctk.CTkLabel(card, text=f"{r.summary()}   →   {r.target}", anchor="w", text_color=MUTED,
                         font=ctk.CTkFont(size=12), wraplength=480, justify="left").grid(
                row=1, column=1, sticky="ew", pady=(0, 12))
            ctl = ctk.CTkFrame(card, fg_color="transparent")
            ctl.grid(row=0, column=2, rowspan=2, padx=12)
            sw = ctk.CTkSwitch(ctl, text="", width=44, progress_color=ACCENT,
                               command=lambda idx=i: self.toggle_rule(idx))
            sw.pack(side="left", padx=(0, 6))
            if r.enabled:
                sw.select()
            for txt, cmd, ok in (("▲", lambda idx=i: self.move_rule(idx, -1), i > 0),
                                 ("▼", lambda idx=i: self.move_rule(idx, 1), i < n - 1),
                                 ("✎", lambda idx=i: self.edit_rule(idx), True),
                                 ("✕", lambda idx=i: self.delete_rule(idx), True)):
                ctk.CTkButton(ctl, text=txt, width=34, height=34, corner_radius=10, fg_color="#232839",
                              hover_color="#30364b" if txt != "✕" else "#7a2a35", command=cmd,
                              state="normal" if ok else "disabled").pack(side="left", padx=2)

    def _drag_start(self, i):
        self._drag_i = i
        if i < len(self._cards):
            self._cards[i].configure(border_width=2)

    def _drag_end(self, event, i):
        if self._drag_i is None:
            return
        self._drag_i, y, target = None, event.y_root, i
        cards = [c for c in self._cards if c.winfo_exists()]
        if cards:
            if y < cards[0].winfo_rooty():
                target = 0
            elif y > cards[-1].winfo_rooty() + cards[-1].winfo_height():
                target = len(cards) - 1
            else:
                for j, c in enumerate(cards):
                    if c.winfo_rooty() <= y <= c.winfo_rooty() + c.winfo_height():
                        target = j
                        break
        if target != i:
            self.cfg.rules.insert(target, self.cfg.rules.pop(i))
            self.after(10, self._commit)
        else:
            self.after(10, self.refresh_rules)

    def _commit(self):
        save_config(self.cfg)
        self.refresh_rules()

    def toggle_rule(self, i):
        self.cfg.rules[i].enabled = not self.cfg.rules[i].enabled
        self._commit()

    def move_rule(self, i, d):
        j = i + d
        if 0 <= j < len(self.cfg.rules):
            self.cfg.rules[i], self.cfg.rules[j] = self.cfg.rules[j], self.cfg.rules[i]
            self._commit()

    def delete_rule(self, i):
        if messagebox.askyesno("Delete rule", f"Delete “{self.cfg.rules[i].name}”?"):
            del self.cfg.rules[i]
            self._commit()

    def edit_rule(self, i):
        def done(new):
            self.cfg.rules[i] = new
            self._commit()
        RuleDialog(self, self.cfg.rules[i], done)

    def new_rule(self):
        def done(new):
            self.cfg.rules.insert(0, new)
            self._commit()
            self.toast("Rule added at top priority")
        RuleDialog(self, Rule(name="", target=""), done)

    def reset_rules(self):
        if messagebox.askyesno("Reset", "Replace all rules with the defaults?"):
            self.cfg.rules[:] = default_rules()
            self._commit()

    # ---- Preview page ---------------------------------------------------
    def _build_preview_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        right = self._page_header(page, "Preview", "See exactly what will happen before anything does.")
        self.sort_btn = self._btn(right, "Sort now", self.sort_now, primary=True, width=120)
        self.sort_btn.pack(side="right", padx=(8, 0))
        self._btn(right, "Rescan", self.scan, width=100).pack(side="right")
        self.preview_info = ctk.CTkLabel(page, text="", text_color=MUTED, anchor="w")
        self.preview_info.pack(fill="x", pady=(16, 6))
        self.progress = ctk.CTkProgressBar(page, progress_color=ACCENT, height=8)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(0, 10))
        self.preview_box = ctk.CTkTextbox(page, fg_color=CARD, corner_radius=14, wrap="none",
                                          font=ctk.CTkFont(family="Consolas", size=13))
        self.preview_box.pack(fill="both", expand=True)
        return page

    def scan(self):
        try:
            self.pending = self.sorter.plan()
        except Exception as exc:
            self.pending = []
            self.toast(f"Scan failed: {exc}")
        box = self.preview_box
        box.configure(state="normal")
        box.delete("1.0", "end")
        root = self.cfg.settings.root
        for m in self.pending[:500]:
            dest = self.sorter._rel(m.dest_dir)
            if m.action == "zip":
                dest = f"{dest}/{m.zip_name}  (zip)"
            else:
                dest += "/"
            box.insert("end", f"{Path(m.src).name}\n    ↳ {dest}   [{m.rule}]\n")
        if len(self.pending) > 500:
            box.insert("end", f"\n… and {len(self.pending) - 500} more")
        if not self.pending:
            box.insert("end", "Nothing to sort - everything already matches your rules ✨")
        box.configure(state="disabled")
        self.preview_info.configure(text=f"{len(self.pending)} file(s) would be handled in  {root}")
        self.progress.set(0)

    def sort_now(self):
        if self.busy:
            return
        if not self.pending:
            self.scan()
        if not self.pending:
            return
        if not messagebox.askyesno("Sort now", f"Process {len(self.pending)} file(s)?\n(You can undo afterwards.)"):
            return
        self._run_batch(list(self.pending))

    def _run_batch(self, moves):
        self.busy = True
        self.sort_btn.configure(state="disabled")

        def work():
            ok, bad = self.sorter.execute(moves, progress=lambda f: self.events.put(("progress", f)),
                                          log=lambda m: self.events.put(("log", m)))
            self.events.put(("done", ok, bad))
        threading.Thread(target=work, daemon=True).start()

    def quick_sort(self):  # from the tray menu, no confirmation
        if self.busy:
            return
        moves = self.sorter.plan()
        if moves:
            self._run_batch(moves)
        else:
            self.toast("Nothing to sort")

    # ---- Activity page --------------------------------------------------
    def _build_activity_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        right = self._page_header(page, "Activity", "Everything the sorter has done this session.")
        self._btn(right, "Open folder", lambda: open_folder(self.cfg.settings.root)).pack(side="right",
                                                                                      padx=(8, 0))
        self._btn(right, "↩  Undo last batch", self.undo).pack(side="right")
        self.log_box = ctk.CTkTextbox(page, fg_color=CARD, corner_radius=14,
                                      font=ctk.CTkFont(family="Consolas", size=13))
        self.log_box.pack(fill="both", expand=True, pady=(18, 0))
        self.log_box.configure(state="disabled")
        return page

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{datetime.now():%H:%M:%S}  {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def undo(self):
        n = self.sorter.undo_last(log=self.log)
        self.toast(f"Restored {n} file(s)" if n else "Nothing to undo")

    # ---- Settings page --------------------------------------------------
    def _build_settings_page(self):
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(page, text="Settings", font=ctk.CTkFont(size=30, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(page, text=f"Where to look and how careful to be.   ·   v{APP_VERSION}",
                     text_color=MUTED).pack(anchor="w")
        card = ctk.CTkFrame(page, fg_color=CARD, corner_radius=16)
        card.pack(fill="x", pady=(18, 0))
        st = self.cfg.settings
        self.set_entries = {}

        def row(label, key, value, browse=False, hint=""):
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=14, weight="bold")).pack(
                anchor="w", padx=22, pady=(18, 0))
            if hint:
                ctk.CTkLabel(card, text=hint, text_color=MUTED, font=ctk.CTkFont(size=12)).pack(
                    anchor="w", padx=22)
            f = ctk.CTkFrame(card, fg_color="transparent")
            f.pack(fill="x", padx=22, pady=(6, 0))
            e = ctk.CTkEntry(f, height=38, corner_radius=10, fg_color="#12141c", border_width=0)
            e.pack(side="left", fill="x", expand=True)
            e.insert(0, str(value))
            if browse:
                ctk.CTkButton(f, text="Browse", width=90, height=38, fg_color="#232839",
                              command=self._browse).pack(side="left", padx=(8, 0))
            self.set_entries[key] = e

        row("Folder to organise", "root", st.root, browse=True)
        row("Settle time (seconds)", "settle_seconds", st.settle_seconds,
            hint="Files modified more recently than this are skipped (protects active downloads).")
        row("Watch interval (seconds)", "poll_seconds", st.poll_seconds)
        row("Folder for unmatched files", "unmatched_folder", st.unmatched_folder,
            hint="Leave empty to keep unmatched files where they are, or e.g. “Other”.")

        ctk.CTkLabel(card, text="Duplicate files", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=22, pady=(18, 0))
        ctk.CTkLabel(card, text="Identical copies (same SHA-256) of files already in the folder tree are "
                                "moved aside, never deleted.", text_color=MUTED,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22)
        self.dup_var = ctk.StringVar(value="Move to Duplicates folder" if st.duplicate_mode == "move" else "Off")
        ctk.CTkOptionMenu(card, values=["Off", "Move to Duplicates folder"], variable=self.dup_var,
                          fg_color="#12141c", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                          height=36).pack(anchor="w", padx=22, pady=(6, 0))

        self.start_switch = ctk.CTkSwitch(card, text="Start watching when the app opens", progress_color=ACCENT)
        self.start_switch.pack(anchor="w", padx=22, pady=(20, 6))
        self.tray_switch = ctk.CTkSwitch(card, text="Keep running in the system tray when the window is closed",
                                         progress_color=ACCENT, state="normal" if HAS_TRAY else "disabled")
        self.tray_switch.pack(anchor="w", padx=22, pady=6)
        self.win_switch = ctk.CTkSwitch(card, text="Launch automatically when Windows starts",
                                        progress_color=ACCENT,
                                        state="normal" if sys.platform.startswith("win") else "disabled")
        self.win_switch.pack(anchor="w", padx=22, pady=6)
        for sw, val in ((self.start_switch, st.watch_on_start), (self.tray_switch, st.keep_in_tray and HAS_TRAY),
                        (self.win_switch, st.start_with_windows)):
            if val:
                sw.select()
        self._btn(card, "Save settings", self.save_settings, primary=True, width=150).pack(
            anchor="w", padx=22, pady=(14, 22))
        return page

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.cfg.settings.root)
        if d:
            e = self.set_entries["root"]
            e.delete(0, "end")
            e.insert(0, d)

    def save_settings(self):
        try:
            st = self.cfg.settings
            root = self.set_entries["root"].get().strip()
            if not Path(root).is_dir():
                raise ValueError("That folder doesn't exist.")
            if Path(root) == Path.home():
                raise ValueError("Please don't point the sorter at your whole home folder.")
            st.root = root
            st.settle_seconds = max(0.0, float(self.set_entries["settle_seconds"].get() or 0))
            st.poll_seconds = max(1.0, float(self.set_entries["poll_seconds"].get() or 4))
            st.unmatched_folder = self.set_entries["unmatched_folder"].get().strip()
            st.duplicate_mode = "move" if self.dup_var.get().startswith("Move") else "off"
            st.watch_on_start = bool(self.start_switch.get())
            st.keep_in_tray = bool(self.tray_switch.get())
            st.start_with_windows = bool(self.win_switch.get())
            set_startup(st.start_with_windows)
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return
        self.sorter._index_t = 0
        save_config(self.cfg)
        self.toast("Settings saved")

    # ---- Watch mode -----------------------------------------------------
    def toggle_watch(self):
        if self.watch_switch.get():
            if self.watcher is None or not self.watcher.is_alive():
                self.stop_evt.clear()
                self.watcher = threading.Thread(target=self._watch_loop, daemon=True)
                self.watcher.start()
            self.status_dot.configure(text="● Watching for new files", text_color="#3ddc97")
            self.toast("Watch mode on - new files are sorted automatically")
        else:
            self.stop_evt.set()
            self.status_dot.configure(text="● Watching is off", text_color=MUTED)

    def _watch_loop(self):
        while not self.stop_evt.is_set():
            try:
                moves = self.sorter.plan()
                if moves:
                    ok, _ = self.sorter.execute(moves, log=lambda m: self.events.put(("log", m)))
                    if ok:
                        self.events.put(("toast", f"Auto-sorted {ok} file(s)"))
            except Exception as exc:
                self.events.put(("log", f"✗ watcher: {exc}"))
            self.stop_evt.wait(self.cfg.settings.poll_seconds)

    # ---- tray -------------------------------------------------------------
    def _start_tray(self):
        if not HAS_TRAY or self.tray:
            return
        put = self.events.put
        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", lambda: put(("show",)), default=True),
            pystray.MenuItem("Sort now", lambda: put(("sortnow",))),
            pystray.MenuItem(lambda item: "Pause watching" if self.watch_switch.get() else "Resume watching",
                             lambda: put(("togglewatch",))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: put(("quit",))),
        )
        self.tray = pystray.Icon("danevo", tray_image(), APP_NAME, menu)
        self.tray.run_detached()

    def _hide_to_tray(self):
        self._start_tray()
        self.withdraw()

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    # ---- events, toast, close ------------------------------------------
    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.log(ev[1])
                elif kind == "progress":
                    self.progress.set(ev[1])
                elif kind == "toast":
                    self.toast(ev[1])
                elif kind == "done":
                    self.busy = False
                    self.sort_btn.configure(state="normal")
                    self.toast(f"Processed {ev[1]} file(s)" + (f", {ev[2]} failed" if ev[2] else ""))
                    self.scan()
                elif kind == "show":
                    self._show_window()
                elif kind == "sortnow":
                    self.quick_sort()
                elif kind == "togglewatch":
                    (self.watch_switch.deselect if self.watch_switch.get() else self.watch_switch.select)()
                    self.toggle_watch()
                elif kind == "quit":
                    self._quit()
                    return
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def toast(self, msg):
        frame = ctk.CTkFrame(self, corner_radius=14, fg_color="#272c3f", border_width=1, border_color=ACCENT)
        ctk.CTkLabel(frame, text=msg).pack(padx=18, pady=12)
        steps = 14

        def slide(i=0):
            if not frame.winfo_exists():
                return
            t = i / steps
            frame.place(relx=1.0, rely=1.0, x=-28, y=-28 + int(60 * (1 - t) ** 2), anchor="se")
            if i < steps:
                self.after(15, lambda: slide(i + 1))
        slide()
        self.after(3400, lambda: frame.destroy() if frame.winfo_exists() else None)

    def _close(self):
        if HAS_TRAY and self.cfg.settings.keep_in_tray:
            self._hide_to_tray()
            if self.watch_switch.get():
                pass  # keeps sorting in the background
            return
        self._quit()

    def _quit(self):
        self.stop_evt.set()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.destroy()


_instance_lock = None


def main():
    global _instance_lock
    if sys.platform.startswith("win"):
        try:  # own taskbar identity so our icon is shown instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Danevo.FileSorter")
        except Exception:
            pass
    _instance_lock = socket.socket()
    try:
        _instance_lock.bind(("127.0.0.1", 47653))
    except OSError:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        messagebox.showinfo(APP_NAME, f"{APP_NAME} is already running.\nLook for it in the system tray.")
        return
    App(minimized="--minimized" in sys.argv).mainloop()


if __name__ == "__main__":
    main()
