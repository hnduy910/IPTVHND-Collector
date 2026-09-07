from __future__ import annotations

import argparse
import json
import pathlib
import sys

from main import Entry, is_definitely_radio, is_target_football, is_target_movie, is_vietnam, parse_m3u, normalize_url


def _read(path: pathlib.Path) -> tuple[list[str], list[Entry]]:
    if not path.exists():
        raise ValueError(f"missing output: {path}")
    return parse_m3u(path.read_text(encoding="utf-8", errors="replace"))


def _urls(entries: list[Entry]) -> list[str]:
    return [normalize_url(entry.url) for entry in entries if normalize_url(entry.url)]


def _assert_unique(name: str, urls: list[str]) -> None:
    if len(urls) != len(set(urls)):
        raise ValueError(f"{name} contains duplicate exact stream URLs")


def validate(archive_path: pathlib.Path, vietnam_path: pathlib.Path, football_path: pathlib.Path,
             movies_path: pathlib.Path, stats_path: pathlib.Path) -> dict[str, int]:
    _archive_header, archive = _read(archive_path)
    _vietnam_header, vietnam = _read(vietnam_path)
    _football_header, football = _read(football_path)
    _movies_header, movies = _read(movies_path)
    stats = json.loads(stats_path.read_text(encoding="utf-8"))

    archive_urls = _urls(archive)
    _assert_unique("Archive", archive_urls)
    archive_set = set(archive_urls)
    if any(is_definitely_radio(entry) for entry in archive):
        raise ValueError("Archive contains high-confidence radio/audio-only metadata")

    playlists = {"Vietnam Live": vietnam, "Football Live": football, "Movies Live": movies}
    for name, entries in playlists.items():
        urls = _urls(entries)
        _assert_unique(name, urls)
        missing = [url for url in urls if url not in archive_set]
        if missing:
            raise ValueError(f"{name} contains {len(missing)} URL(s) absent from Archive")
        if any(is_definitely_radio(entry) for entry in entries):
            raise ValueError(f"{name} contains high-confidence radio/audio-only metadata")

    if any(not is_vietnam(entry) for entry in vietnam):
        raise ValueError("Vietnam Live contains an entry not classified as Vietnam TV")
    if any(not is_target_football(entry) for entry in football):
        raise ValueError("Football Live contains a non-target football entry")
    if any(not is_target_movie(entry) for entry in movies):
        raise ValueError("Movies Live contains a channel outside the curated movie reference")

    required = {
        "schema_version": 2,
        "sources": ("total", "ok", "failed", "newly_discovered", "removed_after_failure_threshold"),
        "archive": ("total_tv_video_urls", "new_urls", "radio_audio_only_rejected", "radio_audio_only_removed"),
        "vietnam": ("archive", "live", "localities_detected", "groups", "health"),
        "football": ("archive", "live", "candidates", "checked", "dead", "timeout", "unsupported", "resolution_rejected", "resolution_unknown", "audio_only_rejected", "live_rate_percent"),
        "movies": ("archive", "live", "candidates", "checked", "dead", "timeout", "unsupported", "resolution_rejected", "resolution_unknown", "audio_only_rejected", "live_rate_percent"),
        "health": ("vietnam", "football", "movies"),
    }
    for key, subkeys in required.items():
        if key not in stats:
            raise ValueError(f"stats.json missing {key}")
        if isinstance(subkeys, tuple):
            missing = [subkey for subkey in subkeys if subkey not in stats[key]]
            if missing:
                raise ValueError(f"stats.json {key} missing {missing}")

    if stats["archive"]["total_tv_video_urls"] != len(archive_urls):
        raise ValueError("stats archive total does not match Archive")
    if stats["vietnam"]["archive"] != len(_urls([entry for entry in archive if is_vietnam(entry)])):
        raise ValueError("stats Vietnam archive total does not match Archive")
    if stats["football"]["live"] != len(_urls(football)) or stats["movies"]["live"] != len(_urls(movies)):
        raise ValueError("stats Live totals do not match playlist outputs")
    for category in ("vietnam", "football", "movies"):
        health = stats["health"][category]
        if health["live"] != stats[category]["live"]:
            raise ValueError(f"stats health/live mismatch for {category}")
        if health["live"] > health["checked"]:
            raise ValueError(f"stats checked count is smaller than live count for {category}")

    return {"archive": len(archive_urls), "vietnam": len(vietnam), "football": len(football), "movies": len(movies)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Archive-to-Live IPTV outputs")
    parser.add_argument("--archive", default="iptvhnd.m3u")
    parser.add_argument("--vietnam", default="iptvhnd-vietnam-live.m3u")
    parser.add_argument("--football", default="iptvhnd-football-live.m3u")
    parser.add_argument("--movies", default="iptvhnd-movies-live.m3u")
    parser.add_argument("--stats", default="stats.json")
    args = parser.parse_args()
    try:
        counts = validate(pathlib.Path(args.archive), pathlib.Path(args.vietnam), pathlib.Path(args.football), pathlib.Path(args.movies), pathlib.Path(args.stats))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    print("VALIDATION OK " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
