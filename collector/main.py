from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

USER_AGENT = "IPTVHND-Collector/1.1"

INTERNATIONAL_KEEP_HINTS = {
    "movie", "movies", "film", "films", "cinema", "cinemas",
    "sport", "sports", "football", "soccer", "futbol", "fútbol",
}


@dataclass(frozen=True)
class Entry:
    pre_lines: tuple[str, ...]
    extinf: str
    post_lines: tuple[str, ...]
    url: str

    def render(self) -> str:
        lines = [*self.pre_lines, self.extinf, *self.post_lines, self.url]
        return "\n".join(lines).rstrip() + "\n"


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8-sig", errors="replace")


def load_sources(path: pathlib.Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def parse_m3u(text: str) -> tuple[list[str], list[Entry]]:
    lines = [x.rstrip("\r") for x in text.splitlines()]
    header: list[str] = []
    entries: list[Entry] = []
    pending: list[str] = []
    current_extinf: str | None = None
    current_post: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            if not header:
                header.append(raw)
            continue
        if line.startswith("#EXTINF"):
            current_extinf = raw
            current_post = []
            continue
        if line.startswith("#"):
            if current_extinf is None:
                pending.append(raw)
            else:
                current_post.append(raw)
            continue
        if current_extinf is not None:
            entries.append(Entry(tuple(pending), current_extinf, tuple(current_post), raw))
            pending = []
            current_extinf = None
            current_post = []
    return header, entries


def attr(extinf: str, key: str) -> str:
    m = re.search(rf'\b{re.escape(key)}="([^"]*)"', extinf, flags=re.I)
    return m.group(1) if m else ""


def entry_text(e: Entry) -> str:
    name = e.extinf.split(",", 1)[1] if "," in e.extinf else ""
    parts = [name, attr(e.extinf, "tvg-name"), attr(e.extinf, "tvg-id"),
             attr(e.extinf, "group-title"), attr(e.extinf, "tvg-country"),
             attr(e.extinf, "country"), " ".join(e.pre_lines), " ".join(e.post_lines)]
    return " ".join(parts).lower()


def is_vietnam(e: Entry, source_url: str = "") -> bool:
    txt = entry_text(e)
    country = (attr(e.extinf, "tvg-country") or attr(e.extinf, "country")).lower()
    if country in {"vn", "vnm", "vietnam", "viet nam", "việt nam"}:
        return True
    if "/countries/vn.m3u" in source_url.lower():
        return True
    if any(h in txt for h in ("vietnam", "viet nam", "việt nam", "vietnamese")):
        return True
    return bool(re.search(r"(?:^|[^a-z0-9])vn(?:[^a-z0-9]|$)", txt))


def category_flags(e: Entry) -> tuple[bool, bool]:
    txt = entry_text(e)
    movie = any(h in txt for h in ("movie", "movies", "film", "films", "cinema", "cinemas"))
    sport = any(h in txt for h in ("sport", "sports", "football", "soccer", "futbol", "fútbol"))
    return movie, sport


def is_movie_or_sport(e: Entry, source_url: str) -> bool:
    surl = source_url.lower()
    if any(x in surl for x in ("/categories/movies.m3u", "/categories/sports.m3u")):
        return True
    movie, sport = category_flags(e)
    return movie or sport


def should_keep(e: Entry, source_url: str) -> bool:
    return is_vietnam(e, source_url) or is_movie_or_sport(e, source_url)


def normalize_url(url: str) -> str:
    return url.strip()


def read_existing(path: pathlib.Path) -> tuple[list[str], list[Entry]]:
    if not path.exists():
        return ["#EXTM3U"], []
    return parse_m3u(path.read_text(encoding="utf-8", errors="replace"))


def merge_header(existing_header: list[str], source_headers: Iterable[list[str]]) -> str:
    if existing_header:
        return existing_header[0]
    for h in source_headers:
        if h:
            return h[0]
    return "#EXTM3U"


def write_stats(path: pathlib.Path, entries: list[Entry], new_urls: int,
                sources_total: int, sources_ok: int, sources_failed: int) -> None:
    vietnam = movies = sports = 0
    for e in entries:
        if is_vietnam(e):
            vietnam += 1
        movie, sport = category_flags(e)
        movies += int(movie)
        sports += int(sport)
    data = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_urls": len({normalize_url(e.url) for e in entries if normalize_url(e.url)}),
        "new_urls": new_urls,
        "vietnam": vietnam,
        "movies": movies,
        "sports": sports,
        "sources_total": sources_total,
        "sources_ok": sources_ok,
        "sources_failed": sources_failed,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Append-only IPTV community playlist collector")
    ap.add_argument("--sources", default="config/sources.txt")
    ap.add_argument("--output", default="iptvhnd.m3u")
    ap.add_argument("--stats", default="stats.json")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()
    source_path = pathlib.Path(args.sources)
    output_path = pathlib.Path(args.output)
    stats_path = pathlib.Path(args.stats)
    sources = load_sources(source_path)
    old_header, old_entries = read_existing(output_path)
    seen = {normalize_url(e.url) for e in old_entries}
    added: list[Entry] = []
    source_headers: list[list[str]] = []
    sources_ok = 0
    sources_failed = 0
    print(f"Existing entries: {len(old_entries)}")
    print(f"Sources: {len(sources)}")
    for src in sources:
        try:
            text = fetch_text(src, timeout=args.timeout)
            header, entries = parse_m3u(text)
            source_headers.append(header)
            sources_ok += 1
            kept = new = 0
            for e in entries:
                if not should_keep(e, src):
                    continue
                kept += 1
                key = normalize_url(e.url)
                if not key or key in seen:
                    continue
                seen.add(key)
                added.append(e)
                new += 1
            print(f"OK {src} entries={len(entries)} kept={kept} new={new}")
        except Exception as exc:
            sources_failed += 1
            print(f"WARN {src}: {exc}", file=sys.stderr)
    header = merge_header(old_header, source_headers)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(header.rstrip() + "\n")
        for e in old_entries:
            f.write(e.render())
        for e in added:
            f.write(e.render())
    all_entries = old_entries + added
    write_stats(stats_path, all_entries, len(added), len(sources), sources_ok, sources_failed)
    print(f"Added: {len(added)}")
    print(f"Total: {len(all_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
