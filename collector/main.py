from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import re
import socket
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

USER_AGENT = "IPTVHND-Collector/1.7"

VIETNAM_PLACE_HINTS = {
    "an giang", "ba ria vung tau", "bac giang", "bac kan", "bac lieu", "bac ninh", "ben tre",
    "binh dinh", "binh duong", "binh phuoc", "binh thuan", "ca mau", "can tho", "cao bang",
    "da nang", "dak lak", "dak nong", "dien bien", "dong nai", "dong thap", "gia lai",
    "ha giang", "ha nam", "ha noi", "ha tinh", "hai duong", "hai phong", "hau giang",
    "hoa binh", "ho chi minh", "hung yen", "khanh hoa", "kien giang", "kon tum", "lai chau",
    "lam dong", "lang son", "lao cai", "long an", "nam dinh", "nghe an", "ninh binh",
    "ninh thuan", "phu tho", "phu yen", "quang binh", "quang nam", "quang ngai", "quang ninh",
    "quang tri", "soc trang", "son la", "tay ninh", "thai binh", "thai nguyen", "thanh hoa",
    "thua thien hue", "hue", "tien giang", "tra vinh", "tuyen quang", "vinh long", "vinh phuc", "yen bai",
}
VIETNAM_NETWORK_PATTERNS = (
    r"\bvtv(?:\d|can\s*tho|cab|go)?\b", r"\bhtv(?:\d|key|sports?|the\s*thao)?\b",
    r"\bthvl(?:\d)?\b", r"\bvtc(?:\d|now)?\b", r"\bsctv(?:\d+)?\b", r"\bantv\b",
    r"\bqpvn\b", r"\bquoc\s*phong\s*viet\s*nam\b", r"\bquoc\s*hoi\b", r"\bvnews\b",
    r"\bhanoitv(?:\d)?\b", r"\bon\s+(?:movies?|sports?|football|life|kids|vie|music|golf)\b",
)
MOVIE_HINTS = ("movie", "movies", "film", "films", "cinema", "cinemas")
SPORT_HINTS = ("sport", "sports", "football", "soccer", "futbol", "fútbol")
GROUP_KEYS = (
    "vtv", "htv", "thvl", "vtc", "sctv", "vietnam_local", "vietnam_other",
    "international_movies", "international_sports",
)


@dataclass(frozen=True)
class Entry:
    pre_lines: tuple[str, ...]
    extinf: str
    post_lines: tuple[str, ...]
    url: str

    def render(self) -> str:
        return "\n".join([*self.pre_lines, self.extinf, *self.post_lines, self.url]).rstrip() + "\n"


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


def load_sources(path: pathlib.Path) -> list[str]:
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_m3u(text: str) -> tuple[list[str], list[Entry]]:
    header, entries, pending, current_post = [], [], [], []
    current_extinf = None
    for raw in [x.rstrip("\r") for x in text.splitlines()]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            if not header:
                header.append(raw)
        elif line.startswith("#EXTINF"):
            current_extinf, current_post = raw, []
        elif line.startswith("#"):
            (pending if current_extinf is None else current_post).append(raw)
        elif current_extinf is not None:
            entries.append(Entry(tuple(pending), current_extinf, tuple(current_post), raw))
            pending, current_extinf, current_post = [], None, []
    return header, entries


def attr(extinf: str, key: str) -> str:
    m = re.search(rf'\b{re.escape(key)}="([^"]*)"', extinf, flags=re.I)
    return m.group(1) if m else ""


def entry_text(e: Entry) -> str:
    name = e.extinf.split(",", 1)[1] if "," in e.extinf else ""
    return " ".join([
        name,
        attr(e.extinf, "tvg-name"),
        attr(e.extinf, "tvg-id"),
        attr(e.extinf, "group-title"),
        attr(e.extinf, "tvg-country"),
        attr(e.extinf, "country"),
        " ".join(e.pre_lines),
        " ".join(e.post_lines),
    ]).lower()


def ascii_text(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).lower()


def is_vietnam(e: Entry, source_url: str = "") -> bool:
    plain = ascii_text(entry_text(e))
    country = ascii_text(attr(e.extinf, "tvg-country") or attr(e.extinf, "country")).strip()
    if country in {"vn", "vnm", "vietnam", "viet nam"} or attr(e.extinf, "tvg-id").lower().strip().endswith(".vn"):
        return True
    if "/countries/vn.m3u" in source_url.lower() or "/languages/vie.m3u" in source_url.lower():
        return True
    if any(h in plain for h in ("vietnam", "viet nam", "vietnamese")):
        return True
    if re.search(r"(?:^|[^a-z0-9])vn(?:[^a-z0-9]|$)", plain):
        return True
    return any(re.search(p, plain) for p in VIETNAM_NETWORK_PATTERNS) or any(p in plain for p in VIETNAM_PLACE_HINTS)


def locality(e: Entry) -> str | None:
    plain = ascii_text(entry_text(e))
    return next((p for p in sorted(VIETNAM_PLACE_HINTS, key=len, reverse=True) if p in plain), None)


def category_flags(e: Entry) -> tuple[bool, bool]:
    txt = entry_text(e)
    return any(h in txt for h in MOVIE_HINTS), any(h in txt for h in SPORT_HINTS)


def is_movie_or_sport(e: Entry, source_url: str) -> bool:
    if any(x in source_url.lower() for x in ("/categories/movies.m3u", "/categories/sports.m3u")):
        return True
    return any(category_flags(e))


def should_keep(e: Entry, source_url: str) -> bool:
    return is_vietnam(e, source_url) or is_movie_or_sport(e, source_url)


def normalize_url(url: str) -> str:
    return url.strip()


def read_existing(path: pathlib.Path) -> tuple[list[str], list[Entry]]:
    return parse_m3u(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else (["#EXTM3U"], [])


def merge_header(existing_header: list[str], source_headers: Iterable[list[str]]) -> str:
    if existing_header:
        return existing_header[0]
    return next((h[0] for h in source_headers if h), "#EXTM3U")


def request_headers_for(e: Entry) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    for line in (*e.pre_lines, *e.post_lines):
        low = line.lower()
        if low.startswith("#extvlcopt:http-user-agent="):
            headers["User-Agent"] = line.split("=", 1)[1].strip()
        elif low.startswith("#extvlcopt:http-referrer=") or low.startswith("#extvlcopt:http-referer="):
            headers["Referer"] = line.split("=", 1)[1].strip()
    return headers


def check_http_stream(e: Entry, timeout: int) -> tuple[str, str]:
    url = normalize_url(e.url)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return "unsupported", parsed.scheme or "unknown"
    headers = request_headers_for(e)
    headers["Range"] = "bytes=0-65535"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            ctype = (resp.headers.get("Content-Type") or "").lower()
            final_url = resp.geturl()
            data = resp.read(65536)
    except urllib.error.HTTPError as exc:
        return "dead", f"http_{exc.code}"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return ("timeout", "timeout") if isinstance(reason, (TimeoutError, socket.timeout)) else ("dead", type(reason).__name__)
    except (TimeoutError, socket.timeout):
        return "timeout", "timeout"
    except Exception as exc:
        return "dead", type(exc).__name__
    if status < 200 or status >= 400 or not data:
        return "dead", f"http_{status}"
    sample = data.decode("utf-8", errors="ignore")
    looks_hls = ".m3u8" in url.lower() or ".m3u8" in final_url.lower() or "mpegurl" in ctype or sample.lstrip().startswith("#EXTM3U")
    if looks_hls:
        if "#EXTM3U" not in sample:
            return "dead", "invalid_hls"
        return ("live", "hls") if any(x in sample for x in ("#EXTINF", "#EXT-X-STREAM-INF", "#EXT-X-TARGETDURATION")) else ("dead", "empty_hls")
    if ctype.startswith("video/") or "mp2t" in ctype or "octet-stream" in ctype or len(data) >= 188:
        return "live", "http_stream"
    return "dead", "unexpected_content"


def health_check(entries: list[Entry], timeout: int, workers: int) -> tuple[list[Entry], dict[str, int]]:
    stats = {"live": 0, "dead": 0, "timeout": 0, "unsupported": 0}
    live = []

    def run(item):
        i, e = item
        status, reason = check_http_stream(e, timeout)
        return i, e, status, reason

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(run, enumerate(entries)))
    for _, e, status, _ in sorted(results, key=lambda x: x[0]):
        stats[status] = stats.get(status, 0) + 1
        if status == "live":
            live.append(e)
    return live, stats


def compact_group(e: Entry) -> str | None:
    plain = ascii_text(entry_text(e))
    if is_vietnam(e):
        if re.search(r"\bvtv(?:\d|can\s*tho|cab|go)?\b", plain):
            return "vtv"
        if re.search(r"\b(?:htv|htvc)(?:\d|key|sports?|the\s*thao)?\b", plain):
            return "htv"
        if re.search(r"\bthvl(?:\d)?\b", plain):
            return "thvl"
        if re.search(r"\bvtc(?:\d|now)?\b", plain):
            return "vtc"
        if re.search(r"\bsctv(?:\d+)?\b", plain):
            return "sctv"
        if locality(e):
            return "vietnam_local"
        return "vietnam_other"
    movie, sport = category_flags(e)
    if sport:
        return "international_sports"
    if movie:
        return "international_movies"
    return None


def compact_group_counts(entries: list[Entry]) -> dict[str, int]:
    counts = {key: 0 for key in GROUP_KEYS}
    for e in entries:
        group = compact_group(e)
        if group:
            counts[group] += 1
    return counts


def count_categories(entries: list[Entry]) -> dict[str, object]:
    vietnam = movies = sports = 0
    provinces = {}
    for e in entries:
        if is_vietnam(e):
            vietnam += 1
            place = locality(e)
            if place:
                provinces[place] = provinces.get(place, 0) + 1
        movie, sport = category_flags(e)
        movies += int(movie)
        sports += int(sport)
    return {
        "total_urls": len({normalize_url(e.url) for e in entries if normalize_url(e.url)}),
        "vietnam": vietnam,
        "movies": movies,
        "sports": sports,
        "vietnam_localities_detected": len(provinces),
        "vietnam_by_locality": dict(sorted(provinces.items())),
        "groups": compact_group_counts(entries),
    }


def write_playlist(path: pathlib.Path, header: str, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(header.rstrip() + "\n")
        for e in entries:
            f.write(e.render())


def write_stats(path: pathlib.Path, archive_entries: list[Entry], live_entries: list[Entry], new_urls: int,
                sources_total: int, sources_ok: int, sources_failed: int, health: dict[str, int]) -> None:
    archive = count_categories(archive_entries)
    archive["new_urls"] = new_urls
    live = count_categories(live_entries)
    checked = health["live"] + health["dead"] + health["timeout"]
    live.update({
        "candidate_urls_from_archive": len(archive_entries),
        "checked_http_urls": checked,
        "dead": health["dead"],
        "timeout": health["timeout"],
        "unsupported": health["unsupported"],
        "live_rate_percent": round(health["live"] / checked * 100, 2) if checked else 0.0,
    })
    data = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {"total": sources_total, "ok": sources_ok, "failed": sources_failed},
        "archive": archive,
        "live": live,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Filtered append-only archive + live-only view of that archive")
    ap.add_argument("--sources", default="config/sources.txt")
    ap.add_argument("--output", default="iptvhnd.m3u")
    ap.add_argument("--live-output", default="iptvhnd-live.m3u")
    ap.add_argument("--stats", default="stats.json")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--health-timeout", type=int, default=8)
    ap.add_argument("--health-workers", type=int, default=32)
    ap.add_argument("--skip-health", action="store_true")
    args = ap.parse_args()

    source_path = pathlib.Path(args.sources)
    output_path = pathlib.Path(args.output)
    live_path = pathlib.Path(args.live_output)
    stats_path = pathlib.Path(args.stats)
    sources = load_sources(source_path)
    old_header, old_entries = read_existing(output_path)
    archive_seen = {normalize_url(e.url) for e in old_entries}
    added = []
    source_headers = []
    sources_ok = sources_failed = 0

    print(f"Existing archive entries: {len(old_entries)}")
    print(f"Sources: {len(sources)}")
    for src in sources:
        try:
            text = fetch_text(src, timeout=args.timeout)
            header, entries = parse_m3u(text)
            source_headers.append(header)
            sources_ok += 1
            archive_kept = archive_new = 0
            for e in entries:
                if not should_keep(e, src):
                    continue
                archive_kept += 1
                key = normalize_url(e.url)
                if not key or key in archive_seen:
                    continue
                archive_seen.add(key)
                added.append(e)
                archive_new += 1
            print(f"OK {src} entries={len(entries)} archive_kept={archive_kept} archive_new={archive_new}")
        except Exception as exc:
            sources_failed += 1
            print(f"WARN {src}: {exc}", file=sys.stderr)

    header = merge_header(old_header, source_headers)
    all_entries = old_entries + added
    write_playlist(output_path, header, all_entries)

    # Live is exactly the health-checked view of the already-filtered, URL-deduplicated archive.
    if args.skip_health:
        _, prior_live = read_existing(live_path)
        allowed = {normalize_url(e.url) for e in all_entries}
        live_entries = [e for e in prior_live if normalize_url(e.url) in allowed]
        health = {"live": len(live_entries), "dead": 0, "timeout": 0, "unsupported": 0}
    else:
        print(f"Health-checking {len(all_entries)} URLs from iptvhnd.m3u...")
        live_entries, health = health_check(all_entries, args.health_timeout, args.health_workers)
        write_playlist(live_path, header, live_entries)

    write_stats(stats_path, all_entries, live_entries, len(added), len(sources), sources_ok, sources_failed, health)
    print(f"Archive added: {len(added)}")
    print(f"Archive total: {len(all_entries)}")
    print(f"Live candidates from archive: {len(all_entries)}")
    print(f"Live total: {len(live_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())