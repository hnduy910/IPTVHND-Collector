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
from typing import Iterable, Mapping


def _project_version() -> str:
    version_path = pathlib.Path(__file__).resolve().parents[1] / "VERSION"
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        value = "1.12"
    return value or "1.12"


PROJECT_VERSION = _project_version()
USER_AGENT = f"IPTVHND-Collector/{PROJECT_VERSION}"
FHD_WIDTH = 1920
FHD_HEIGHT = 1080

VIETNAM_PLACE_HINTS = {
    "an giang", "ba ria vung tau", "bac giang", "bac kan", "bac lieu", "bac ninh", "ben tre",
    "binh dinh", "binh duong", "binh phuoc", "binh thuan", "ca mau", "can tho", "cao bang",
    "da nang", "dak lak", "dak nong", "dien bien", "dong nai", "dong thap", "gia lai",
    "ha giang", "ha nam", "ha noi", "ha tinh", "hai duong", "hai phong", "hau giang",
    "hoa binh", "ho chi minh", "hung yen", "khanh hoa", "kien giang", "kon tum", "lai chau",
    "lam dong", "lang son", "lao cai", "long an", "nam dinh", "nghe an", "ninh binh",
    "ninh thuan", "phu tho", "phu yen", "quang binh", "quang nam", "quang ngai", "quang ninh",
    "quang tri", "soc trang", "son la", "tay ninh", "thai binh", "thai nguyen", "thanh hoa",
    "thua thien hue", "hue", "tien giang", "tra vinh", "tuyen quang", "vinh long", "vinh phuc",
    "yen bai",
}

VIETNAM_NETWORK_PATTERNS = (
    r"\bvtv(?:\s*\d|\s*can\s*tho|\s*cab|\s*go)?\b",
    r"\bhtv(?:\s*\d|\s*key|\s*sports?|\s*the\s*thao)?\b",
    r"\bhtvc(?:\s*\d|\s*movie|\s*the\s*thao)?\b",
    r"\bthvl(?:\s*\d)?\b",
    r"\bvtc(?:\s*\d|\s*now)?\b",
    r"\bsctv(?:\s*\d+)?\b",
    r"\b(?:vtvcab|fpt\s*play)\b",
    r"\bantv\b", r"\bqpvn\b", r"\bquoc\s*phong\s*viet\s*nam\b",
    r"\bquoc\s*hoi\b", r"\bvnews\b", r"\bhanoi\s*tv(?:\s*\d)?\b",
    r"\bon\s+(?:movies?|sports?|football|life|kids|vie|music|golf|phim)\b",
)

MOVIE_HINTS = ("movie", "movies", "film", "films", "cinema", "cinemas")
SPORT_HINTS = ("sport", "sports", "football", "soccer", "futbol", "fútbol")

# These are channel/family aliases, not stream sources. They are deliberately
# narrower than a generic "movie" keyword so a random VOD or movie playlist is
# not promoted to the Movies Live output.
MOVIE_CHANNEL_PATTERNS = (
    r"\bhbo(?:\s+(?:hd|2|family|signature|asia|hits|comedy))?\b",
    r"\bcinemax(?:\s+(?:hd|2|asia))?\b",
    r"\bwarner\s*tv\b", r"\bbox\s+movies?\b",
    r"\bon\s+(?:movies?|phim\s+viet|cine|vie\s+dramas?)\b",
    r"\b(?:star\s+movies?|fox\s+movies?|sony\s+(?:movies?|max))\b",
    r"\b(?:axn\s+(?:movies?|black|white)|amc\s+(?:crime|stories|select))\b",
    r"\b(?:tcm|turner\s+classic\s+movies?|sky\s+cinema|film4)\b",
    r"\b(?:hallmark\s+movies?|lifetime\s+movies?|movie\s+sphere|movieplex)\b",
    r"\b(?:a&e\s+movies?|cinema\s+world|cinema\s+one)\b",
)

FOOTBALL_COMPETITION_PATTERNS = (
    r"\b(?:uefa\s+)?champions?\s+league\b", r"\bucl\b",
    r"\b(?:uefa\s+)?europa\s+league\b", r"\bconference\s+league\b",
    r"\bpremier\s+league\b", r"\bfa\s+cup\b", r"\bcarabao\s+cup\b",
    r"\befl\s+cup\b", r"\bserie\s+a\b", r"\bcoppa\s+italia\b",
    r"\bla\s+liga\b", r"\bcopa\s+del\s+rey\b", r"\bbundesliga\b",
    r"\bdfb[- ]pokal\b", r"\bligue\s+1\b",
    r"\bcopa\s+libertadores\b", r"\bcopa\s+sudamericana\b",
    r"\bbrasileir(?:ao|a)\b", r"\b(?:brazil|argentina)\s+(?:football|soccer|liga|league)\b",
)
FOOTBALL_DIRECT_PATTERNS = (r"\bfootball\b", r"\bsoccer\b", r"\bfutbol\b")

PROTECTED_PATTERNS = (
    r"\bnetflix\b", r"\bwidevine\b", r"\bfairplay\b", r"\bclearkey\b",
    r"\bpssh\b", r"\bdrm\b", r"\blicense(?:url|key)?\s*=", r"\b(?:token|auth)\s*=",
)
AUDIO_SUFFIXES = (".mp3", ".aac", ".m4a", ".oga", ".ogg", ".opus", ".wav", ".flac")

GROUP_KEYS = (
    "vtv", "htv", "thvl", "vtc", "sctv", "vietnam_local", "vietnam_other",
    "international_movies", "international_football",
)
VIETNAM_FAMILY_KEYS = (
    "VTV", "HTV/HTVC", "THVL", "VTC", "SCTV", "VTVcab/ON", "Vietnam local", "Vietnam other",
)


@dataclass(frozen=True)
class Entry:
    pre_lines: tuple[str, ...]
    extinf: str
    post_lines: tuple[str, ...]
    url: str

    def render(self) -> str:
        return "\n".join([*self.pre_lines, self.extinf, *self.post_lines, self.url]).rstrip() + "\n"


@dataclass(frozen=True)
class StreamProbe:
    status: str
    reason: str
    content_type: str = ""
    final_url: str = ""
    resolutions: tuple[tuple[int, int], ...] = ()
    video: bool | None = None
    audio: bool | None = None


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(8_000_000).decode("utf-8-sig", errors="replace")


def load_sources(path: pathlib.Path) -> list[str]:
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def parse_m3u(text: str) -> tuple[list[str], list[Entry]]:
    """Parse M3U while retaining global headers and per-entry directives."""
    header: list[str] = []
    entries: list[Entry] = []
    pending: list[str] = []
    current_extinf: str | None = None
    current_post: list[str] = []
    seen_entry = False

    for raw in (line.rstrip("\r") for line in text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            current_extinf = raw
            current_post = []
            continue
        if line.startswith("#"):
            if current_extinf is None:
                if not seen_entry:
                    header.append(raw)
                else:
                    pending.append(raw)
            else:
                current_post.append(raw)
            continue
        if current_extinf is not None:
            entries.append(Entry(tuple(pending), current_extinf, tuple(current_post), raw))
            pending = []
            current_extinf = None
            current_post = []
            seen_entry = True

    return header, entries


def attr(extinf: str, key: str) -> str:
    match = re.search(rf'\b{re.escape(key)}="([^"]*)"', extinf, flags=re.I)
    return match.group(1) if match else ""


def channel_name(e: Entry) -> str:
    return e.extinf.split(",", 1)[1] if "," in e.extinf else ""


def entry_text(e: Entry) -> str:
    return " ".join([
        channel_name(e),
        attr(e.extinf, "tvg-name"),
        attr(e.extinf, "tvg-id"),
        attr(e.extinf, "group-title"),
        attr(e.extinf, "tvg-country"),
        attr(e.extinf, "country"),
        " ".join(e.pre_lines),
        " ".join(e.post_lines),
    ]).lower()


def identity_text(e: Entry) -> str:
    """Text used for channel classification, excluding the URL itself."""
    return " ".join([
        channel_name(e), attr(e.extinf, "tvg-name"), attr(e.extinf, "tvg-id"),
        attr(e.extinf, "group-title"),
    ])


def ascii_text(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).lower()


def is_protected_source(e: Entry) -> bool:
    haystack = ascii_text(entry_text(e) + " " + e.url)
    return any(re.search(pattern, haystack) for pattern in PROTECTED_PATTERNS)


def _has_video_word(e: Entry) -> bool:
    text = ascii_text(identity_text(e))
    return bool(re.search(r"\b(?:tv|television|video|channel|live\s*tv)\b", text))


def is_definitely_radio(e: Entry) -> bool:
    """Return true only for high-confidence radio/audio-only metadata.

    A word such as ``music`` is intentionally not enough: music television is
    still video. URL extensions and explicit radio/audio metadata are strong
    signals; actual audio-only HLS is identified by the stream probe below.
    """
    text = ascii_text(entry_text(e))
    identity = ascii_text(identity_text(e))
    radio_identity = ascii_text(" ".join([channel_name(e), attr(e.extinf, "tvg-name"), attr(e.extinf, "group-title")]))
    group = ascii_text(attr(e.extinf, "group-title"))
    explicit = (
        re.search(r"\bradio(?:\s+station)?\b", text)
        or re.search(r"\b(?:internet|web)\s+radio\b", text)
        or re.search(r"\bpodcast\b", text)
        or re.search(r"\bvov\s+radio\b", text)
        or re.search(r"\baudio[- ]only\b", text)
        or re.search(r"\baudio\b", group)
    )
    # Do not inspect tvg-id for the bare FM/AM token: country-code IDs such as
    # ``something.am`` are not radio stations.
    fm_am = re.search(r"\b(?:fm|am)(?:\s*(?:\d{2,3}(?:\.\d+)?|radio|station))?\b", radio_identity)
    extension = urllib.parse.urlsplit(e.url).path.lower()
    if explicit or fm_am:
        # Keep a clearly labelled television/video channel if it happens to
        # include a broadcaster's radio brand in auxiliary metadata.
        if _has_video_word(e) and not (extension.endswith(AUDIO_SUFFIXES) or fm_am and "radio" in identity):
            return False
        return True
    return extension.endswith(AUDIO_SUFFIXES)


def is_vietnam(e: Entry, source_url: str = "") -> bool:
    plain = ascii_text(entry_text(e))
    country = ascii_text(attr(e.extinf, "tvg-country") or attr(e.extinf, "country")).strip()
    if country in {"vn", "vnm", "vietnam", "viet nam"} or attr(e.extinf, "tvg-id").lower().strip().endswith(".vn"):
        return True
    source_lower = source_url.lower()
    if "/countries/vn." in source_lower or "/languages/vie." in source_lower:
        return True
    if any(h in plain for h in ("vietnam", "viet nam", "vietnamese")):
        return True
    if re.search(r"(?:^|[^a-z0-9])vn(?:[^a-z0-9]|$)", plain):
        return True
    return any(re.search(pattern, plain) for pattern in VIETNAM_NETWORK_PATTERNS) or any(
        place in plain for place in VIETNAM_PLACE_HINTS
    )


def locality(e: Entry) -> str | None:
    plain = ascii_text(entry_text(e))
    return next((place for place in sorted(VIETNAM_PLACE_HINTS, key=len, reverse=True) if place in plain), None)


def is_target_movie(e: Entry) -> bool:
    identity = ascii_text(identity_text(e))
    if is_protected_source(e):
        return False
    return any(re.search(pattern, identity) for pattern in MOVIE_CHANNEL_PATTERNS)


def is_target_football(e: Entry) -> bool:
    identity = ascii_text(identity_text(e))
    if is_protected_source(e):
        return False
    return any(re.search(pattern, identity) for pattern in FOOTBALL_COMPETITION_PATTERNS + FOOTBALL_DIRECT_PATTERNS)


def category_flags(e: Entry) -> tuple[bool, bool]:
    return is_target_movie(e), is_target_football(e)


def is_movie_or_sport(e: Entry, source_url: str = "") -> bool:
    # Source categories are discovery hints only. They must not turn a
    # generic sports or movie playlist into a retained stream.
    del source_url
    movie, football = category_flags(e)
    return movie or football


def should_keep(e: Entry, source_url: str) -> bool:
    if is_definitely_radio(e) or is_protected_source(e):
        return False
    return is_vietnam(e, source_url) or is_target_football(e) or is_target_movie(e)


def normalize_url(url: str) -> str:
    # Exact URL dedupe: trimming transport whitespace is safe, but no host,
    # query, case, or path normalization is performed.
    return url.strip()


def migrate_archive(entries: list[Entry]) -> tuple[list[Entry], list[Entry]]:
    kept, removed = [], []
    for entry in entries:
        if is_definitely_radio(entry):
            removed.append(entry)
        else:
            kept.append(entry)
    return kept, removed


def dedupe_archive_entries(entries: list[Entry]) -> tuple[list[Entry], int]:
    """Remove only exact duplicate URLs, retaining the first metadata record."""
    kept: list[Entry] = []
    seen: set[str] = set()
    removed = 0
    for entry in entries:
        key = normalize_url(entry.url)
        if key and key in seen:
            removed += 1
            continue
        if key:
            seen.add(key)
        kept.append(entry)
    return kept, removed


def read_existing(path: pathlib.Path) -> tuple[list[str], list[Entry]]:
    return parse_m3u(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else (["#EXTM3U"], [])


def merge_header(existing_header: list[str], source_headers: Iterable[list[str]]) -> str:
    lines: list[str] = []
    for candidate in [existing_header, *source_headers]:
        for line in candidate:
            if line not in lines:
                lines.append(line)
    return "\n".join(lines) if lines else "#EXTM3U"


def request_headers_for(e: Entry) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    user_agent = attr(e.extinf, "http-user-agent") or attr(e.extinf, "user-agent")
    referrer = attr(e.extinf, "http-referrer") or attr(e.extinf, "http-referer") or attr(e.extinf, "referrer")
    if user_agent:
        headers["User-Agent"] = user_agent
    if referrer:
        headers["Referer"] = referrer
    for line in (*e.pre_lines, *e.post_lines):
        low = line.lower()
        if low.startswith("#extvlcopt:http-user-agent="):
            headers["User-Agent"] = line.split("=", 1)[1].strip()
        elif low.startswith("#extvlcopt:http-referrer=") or low.startswith("#extvlcopt:http-referer="):
            headers["Referer"] = line.split("=", 1)[1].strip()
    return headers


def _hls_attributes(line: str) -> dict[str, str]:
    payload = line.split(":", 1)[1] if ":" in line else ""
    return {match.group(1).upper(): match.group(2).strip('"') for match in re.finditer(r"([A-Z0-9-]+)=(\"[^\"]*\"|[^,]*)", payload)}


def _codec_has_video(codecs: str) -> bool:
    return any(codec.strip().lower().startswith(("avc", "hvc", "hev", "vp8", "vp9", "av01", "theora")) for codec in codecs.split(","))


def _codec_has_audio(codecs: str) -> bool:
    return any(codec.strip().lower().startswith(("mp4a", "ac-3", "ec-3", "opus", "vorbis", "ac3", "eac3")) for codec in codecs.split(","))


def _parse_resolution(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)x(\d+)\s*", value or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _probe_hls(text: str, content_type: str, final_url: str) -> StreamProbe:
    if "#EXTM3U" not in text.upper():
        return StreamProbe("dead", "invalid_hls", content_type, final_url)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[dict[str, str]] = []
    for line in lines:
        if line.upper().startswith("#EXT-X-STREAM-INF"):
            variants.append(_hls_attributes(line))

    resolutions = tuple(resolution for attrs in variants if (resolution := _parse_resolution(attrs.get("RESOLUTION", ""))))
    audio_tags = [line for line in lines if line.upper().startswith("#EXT-X-MEDIA") and _hls_attributes(line).get("TYPE", "").upper() == "AUDIO"]
    video_tags = [line for line in lines if line.upper().startswith("#EXT-X-MEDIA") and _hls_attributes(line).get("TYPE", "").upper() == "VIDEO"]

    if variants:
        codecs = [attrs.get("CODECS", "") for attrs in variants]
        # A variant URI without CODECS/RESOLUTION is ambiguous, not proof of
        # audio-only. Keep it eligible for Vietnam and let FHD-gated groups
        # reject it as resolution_unknown.
        any_video = bool(resolutions) or any(_codec_has_video(codec) or not codec for codec in codecs)
        any_audio = any(_codec_has_audio(codec) for codec in codecs) or bool(audio_tags)
        if not any_video and any_audio:
            return StreamProbe("audio_only", "audio_only_hls", content_type, final_url, resolutions, False, True)
        return StreamProbe("live", "hls_master", content_type, final_url, resolutions, any_video, any_audio or None)

    has_media_segments = any(line.upper().startswith(("#EXTINF", "#EXT-X-TARGETDURATION", "#EXT-X-PART")) for line in lines)
    segment_urls = [line for line in lines if not line.startswith("#")]
    audio_segments = bool(segment_urls) and all(
        urllib.parse.urlsplit(line).path.lower().endswith(AUDIO_SUFFIXES) for line in segment_urls
    )
    if has_media_segments and audio_segments:
        return StreamProbe("audio_only", "audio_only_hls", content_type, final_url, (), False, True)
    if audio_tags and not video_tags and not has_media_segments:
        return StreamProbe("audio_only", "audio_only_hls", content_type, final_url, (), False, True)
    if not has_media_segments and not video_tags and not audio_tags:
        return StreamProbe("dead", "empty_hls", content_type, final_url)
    # A media playlist normally carries no resolution metadata. It is valid
    # for Vietnam Live, but remains unknown for FHD-gated categories.
    return StreamProbe("live", "hls_media", content_type, final_url, (), None, True if has_media_segments else None)


def _probe_dash(text: str, content_type: str, final_url: str) -> StreamProbe:
    if not re.search(r"<\s*MPD\b", text, flags=re.I):
        return StreamProbe("dead", "invalid_dash", content_type, final_url)
    resolutions = tuple(
        (int(width), int(height))
        for width, height in re.findall(r"<Representation\b[^>]*\bwidth=[\"'](\d+)[\"'][^>]*\bheight=[\"'](\d+)[\"']", text, flags=re.I)
    )
    has_video = bool(resolutions or re.search(r"(?:contentType|mimeType)=[\"']video", text, flags=re.I))
    has_audio = bool(re.search(r"(?:contentType|mimeType)=[\"']audio", text, flags=re.I))
    if not has_video and has_audio:
        return StreamProbe("audio_only", "audio_only_dash", content_type, final_url, resolutions, False, True)
    if not has_video and not has_audio:
        return StreamProbe("dead", "empty_dash", content_type, final_url)
    return StreamProbe("live", "dash", content_type, final_url, resolutions, has_video, has_audio or None)


def _fetch_stream_sample(e: Entry, timeout: int) -> StreamProbe:
    url = normalize_url(e.url)
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return StreamProbe("unsupported", parsed.scheme or "unknown", final_url=url)
    headers = request_headers_for(e)
    headers["Range"] = "bytes=0-262143"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            content_type = (resp.headers.get("Content-Type") or "").lower()
            final_url = resp.geturl()
            data = resp.read(262144)
    except urllib.error.HTTPError as exc:
        return StreamProbe("dead", f"http_{exc.code}", final_url=url)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return StreamProbe("timeout", "timeout", final_url=url)
        return StreamProbe("dead", type(reason).__name__, final_url=url)
    except (TimeoutError, socket.timeout):
        return StreamProbe("timeout", "timeout", final_url=url)
    except Exception as exc:
        return StreamProbe("dead", type(exc).__name__, final_url=url)

    if status < 200 or status >= 400 or not data:
        return StreamProbe("dead", f"http_{status}", content_type, final_url)
    sample = data.decode("utf-8", errors="ignore")
    final_path = urllib.parse.urlsplit(final_url or url).path.lower()
    looks_hls = (
        ".m3u8" in url.lower() or ".m3u8" in final_url.lower() or "mpegurl" in content_type
        or sample.lstrip().upper().startswith("#EXTM3U")
    )
    looks_dash = ".mpd" in url.lower() or ".mpd" in final_url.lower() or "dash+xml" in content_type or "<mpd" in sample.lower()
    if looks_hls:
        return _probe_hls(sample, content_type, final_url)
    if looks_dash:
        return _probe_dash(sample, content_type, final_url)
    if content_type.startswith("audio/") or final_path.endswith(AUDIO_SUFFIXES):
        return StreamProbe("audio_only", "audio_content", content_type, final_url, (), False, True)
    if content_type.startswith("video/") or "mp2t" in content_type or "octet-stream" in content_type or len(data) >= 188:
        return StreamProbe("live", "http_stream", content_type, final_url, (), True, None)
    return StreamProbe("dead", "unexpected_content", content_type, final_url)


def _classify_probe(probe: StreamProbe, require_fhd: bool = False) -> tuple[str, str]:
    if probe.status != "live":
        return probe.status, probe.reason
    if probe.video is False:
        return "audio_only", "audio_only"
    if not require_fhd:
        return "live", probe.reason
    if not probe.resolutions:
        return "resolution_unknown", "resolution_unknown"
    if any(width >= FHD_WIDTH and height >= FHD_HEIGHT for width, height in probe.resolutions):
        return "live", probe.reason
    return "resolution_rejected", "below_1080p"


def probe_http_stream(e: Entry, timeout: int) -> StreamProbe:
    return _fetch_stream_sample(e, timeout)


def check_http_stream(e: Entry, timeout: int, require_fhd: bool = False) -> tuple[str, str]:
    return _classify_probe(probe_http_stream(e, timeout), require_fhd=require_fhd)


def _unique_entries(entries: Iterable[Entry]) -> list[Entry]:
    out, seen = [], set()
    for entry in entries:
        key = normalize_url(entry.url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _empty_health(candidates: int = 0) -> dict[str, int | float]:
    return {
        "candidates": candidates,
        "checked": 0,
        "live": 0,
        "dead": 0,
        "timeout": 0,
        "unsupported": 0,
        "audio_only_rejected": 0,
        "resolution_rejected": 0,
        "resolution_unknown": 0,
        "live_rate_percent": 0.0,
    }


def _evaluate_health(entries: list[Entry], probes: Mapping[str, StreamProbe], require_fhd: bool) -> tuple[list[Entry], dict[str, int | float], set[str]]:
    unique = _unique_entries(entries)
    stats = _empty_health(len(unique))
    live: list[Entry] = []
    audio_only_urls: set[str] = set()
    for entry in unique:
        key = normalize_url(entry.url)
        status, _reason = _classify_probe(probes.get(key, StreamProbe("dead", "missing_probe")), require_fhd=require_fhd)
        if status != "unsupported":
            stats["checked"] += 1
        if status == "live":
            stats["live"] += 1
            live.append(entry)
        elif status == "dead":
            stats["dead"] += 1
        elif status == "timeout":
            stats["timeout"] += 1
        elif status == "unsupported":
            stats["unsupported"] += 1
        elif status == "audio_only":
            stats["audio_only_rejected"] += 1
            audio_only_urls.add(key)
        elif status == "resolution_rejected":
            stats["resolution_rejected"] += 1
        elif status == "resolution_unknown":
            stats["resolution_unknown"] += 1
    checked = int(stats["checked"])
    stats["live_rate_percent"] = round(int(stats["live"]) / checked * 100, 2) if checked else 0.0
    return live, stats, audio_only_urls


def _collect_probes(entries: Iterable[Entry], timeout: int, workers: int) -> dict[str, StreamProbe]:
    unique = _unique_entries(entries)
    if not unique:
        return {}

    def run(entry: Entry) -> tuple[str, StreamProbe]:
        return normalize_url(entry.url), probe_http_stream(entry, timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return dict(pool.map(run, unique))


def health_check(entries: list[Entry], timeout: int, workers: int, require_fhd: bool = False) -> tuple[list[Entry], dict[str, int | float]]:
    unique = _unique_entries(entries)
    probes = _collect_probes(unique, timeout, workers)
    live, stats, _ = _evaluate_health(unique, probes, require_fhd=require_fhd)
    # Retain the original health_check shape for callers outside the collector.
    return live, stats


def health_check_categories(
    categories: Mapping[str, list[Entry]], timeout: int, workers: int,
) -> tuple[dict[str, tuple[list[Entry], dict[str, int | float]]], set[str]]:
    all_candidates: list[Entry] = []
    for entries in categories.values():
        all_candidates.extend(entries)
    probes = _collect_probes(all_candidates, timeout, workers)
    results: dict[str, tuple[list[Entry], dict[str, int | float]]] = {}
    audio_only_urls: set[str] = set()
    for category, entries in categories.items():
        live, stats, rejected = _evaluate_health(entries, probes, require_fhd=category in {"football", "movies"})
        results[category] = (live, stats)
        audio_only_urls.update(rejected)
    return results, audio_only_urls


def compact_group(e: Entry) -> str | None:
    plain = ascii_text(entry_text(e))
    if is_vietnam(e):
        if re.search(r"\bvtv(?:\s*\d|\s*can\s*tho|\s*cab|\s*go)?\b", plain):
            return "vtv"
        if re.search(r"\b(?:htv|htvc)(?:\s*\d|\s*key|\s*sports?|\s*the\s*thao)?\b", plain):
            return "htv"
        if re.search(r"\bthvl(?:\s|-)?\d?\b", plain) or re.search(r"\b(?:dai\s+)?truyen\s+hinh\s+vinh\s+long(?:\s*\d)?\b", plain):
            return "thvl"
        if re.search(r"\bvtc(?:\s|-)?(?:\d+|now)?\b", plain) or "vtc digital" in plain or "ky thuat so vtc" in plain or "kts vtc" in plain:
            return "vtc"
        if re.search(r"\bsctv(?:\s*\d+)?\b", plain):
            return "sctv"
        if vietnam_family(e) == "VTVcab/ON":
            return "vietnam_other"
        if locality(e):
            return "vietnam_local"
        return "vietnam_other"
    movie, football = category_flags(e)
    if football:
        return "international_football"
    if movie:
        return "international_movies"
    return None


def compact_group_counts(entries: list[Entry]) -> dict[str, int]:
    counts = {key: 0 for key in GROUP_KEYS}
    for entry in entries:
        group = compact_group(entry)
        if group:
            counts[group] += 1
    return counts


def vietnam_family(e: Entry) -> str:
    plain = ascii_text(identity_text(e) + " " + entry_text(e))
    if re.search(r"\bvtv(?:\s*\d|\s*cab|\s*go)?\b", plain):
        return "VTV"
    if re.search(r"\b(?:htv|htvc)\b", plain):
        return "HTV/HTVC"
    if re.search(r"\bthvl\b|vinh\s+long", plain):
        return "THVL"
    if re.search(r"\bvtc\b", plain):
        return "VTC"
    if re.search(r"\bsctv\b", plain):
        return "SCTV"
    if re.search(r"\bvtvcab\b|\bon\s+(?:movies?|sports?|football|life|kids|vie|music|golf|phim|cine)\b", plain):
        return "VTVcab/ON"
    if locality(e):
        return "Vietnam local"
    return "Vietnam other"


def _category_entries(archive_entries: list[Entry]) -> dict[str, list[Entry]]:
    return {
        "vietnam": [entry for entry in archive_entries if is_vietnam(entry)],
        "football": [entry for entry in archive_entries if is_target_football(entry)],
        "movies": [entry for entry in archive_entries if is_target_movie(entry)],
    }


def _unique_url_count(entries: Iterable[Entry]) -> int:
    return len({normalize_url(entry.url) for entry in entries if normalize_url(entry.url)})


def count_categories(entries: list[Entry]) -> dict[str, object]:
    vietnam_entries = [entry for entry in entries if is_vietnam(entry)]
    movie_entries = [entry for entry in entries if is_target_movie(entry)]
    football_entries = [entry for entry in entries if is_target_football(entry)]
    provinces: dict[str, int] = {}
    for entry in vietnam_entries:
        place = locality(entry)
        if place:
            provinces[place] = provinces.get(place, 0) + 1
    return {
        "total_urls": _unique_url_count(entries),
        "vietnam": _unique_url_count(vietnam_entries),
        "movies": _unique_url_count(movie_entries),
        "football": _unique_url_count(football_entries),
        "sports": _unique_url_count(football_entries),
        "vietnam_localities_detected": len(provinces),
        "vietnam_by_locality": dict(sorted(provinces.items())),
        "groups": compact_group_counts(entries),
    }


def _family_counts(archive_entries: list[Entry], live_entries: list[Entry]) -> dict[str, dict[str, int]]:
    result = {key: {"archive": 0, "live": 0} for key in VIETNAM_FAMILY_KEYS}
    for entry in _unique_entries(archive_entries):
        if is_vietnam(entry):
            result[vietnam_family(entry)]["archive"] += 1
    for entry in _unique_entries(live_entries):
        if is_vietnam(entry):
            result[vietnam_family(entry)]["live"] += 1
    return result


def _locality_counts(entries: list[Entry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in _unique_entries(entries):
        if is_vietnam(entry) and (place := locality(entry)):
            counts[place] = counts.get(place, 0) + 1
    return dict(sorted(counts.items()))


def _union_live_entries(category_live: Mapping[str, list[Entry]]) -> list[Entry]:
    return _unique_entries([entry for category in ("vietnam", "football", "movies") for entry in category_live.get(category, [])])


def build_stats(
    archive_entries: list[Entry],
    category_live: Mapping[str, list[Entry]],
    health: Mapping[str, dict[str, int | float]],
    new_urls: int,
    sources_total: int,
    sources_ok: int,
    sources_failed: int,
    radio_audio_only_rejected: int = 0,
    radio_audio_only_removed: int = 0,
    discovery_meta: Mapping[str, object] | None = None,
) -> dict[str, object]:
    categories = _category_entries(archive_entries)
    live_union = _union_live_entries(category_live)
    discovery_meta = discovery_meta or {}

    vietnam_archive = categories["vietnam"]
    vietnam_live = category_live.get("vietnam", [])
    football_archive = categories["football"]
    football_live = category_live.get("football", [])
    movies_archive = categories["movies"]
    movies_live = category_live.get("movies", [])

    sources = {
        "total": sources_total,
        "ok": sources_ok,
        "failed": sources_failed,
        "newly_discovered": int(discovery_meta.get("newly_discovered_sources", 0) or 0),
        "removed_after_failure_threshold": int(discovery_meta.get("removed_after_failure_threshold", 0) or 0),
    }
    archive = {
        "total_tv_video_urls": _unique_url_count(archive_entries),
        "total_urls": _unique_url_count(archive_entries),
        "new_urls": new_urls,
        "radio_audio_only_rejected": radio_audio_only_rejected,
        "radio_audio_only_removed": radio_audio_only_removed,
        "vietnam": _unique_url_count(vietnam_archive),
        "movies": _unique_url_count(movies_archive),
        "football": _unique_url_count(football_archive),
        "sports": _unique_url_count(football_archive),
        "vietnam_localities_detected": len(_locality_counts(vietnam_archive)),
        "vietnam_by_locality": _locality_counts(vietnam_archive),
        "groups": compact_group_counts(archive_entries),
    }
    live = {
        "total_tv_video_urls": _unique_url_count(live_union),
        "total_urls": _unique_url_count(live_union),
        "vietnam": _unique_url_count(vietnam_live),
        "football": _unique_url_count(football_live),
        "movies": _unique_url_count(movies_live),
        "sports": _unique_url_count(football_live),
        "groups": compact_group_counts(live_union),
        "playlists": {
            "iptvhnd-vietnam-live.m3u": _unique_url_count(vietnam_live),
            "iptvhnd-football-live.m3u": _unique_url_count(football_live),
            "iptvhnd-movies-live.m3u": _unique_url_count(movies_live),
        },
    }

    vietnam = {
        "archive": _unique_url_count(vietnam_archive),
        "live": _unique_url_count(vietnam_live),
        "localities_detected": len(_locality_counts(vietnam_archive)),
        "by_locality": _locality_counts(vietnam_archive),
        "groups": _family_counts(archive_entries, vietnam_live),
    }
    football = {
        "archive": _unique_url_count(football_archive),
        "live": _unique_url_count(football_live),
        **health.get("football", _empty_health(len(football_archive))),
    }
    movies = {
        "archive": _unique_url_count(movies_archive),
        "live": _unique_url_count(movies_live),
        **health.get("movies", _empty_health(len(movies_archive))),
    }
    return {
        "schema_version": 2,
        "collector_version": PROJECT_VERSION,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "archive": archive,
        "live": live,
        "vietnam": {**vietnam, "health": health.get("vietnam", _empty_health(len(vietnam_archive)))},
        "football": football,
        "movies": movies,
        "health": {
            "vietnam": health.get("vietnam", _empty_health(len(vietnam_archive))),
            "football": health.get("football", _empty_health(len(football_archive))),
            "movies": health.get("movies", _empty_health(len(movies_archive))),
        },
    }


def write_stats(
    path: pathlib.Path,
    archive_entries: list[Entry],
    live_entries: list[Entry],
    new_urls: int,
    sources_total: int,
    sources_ok: int,
    sources_failed: int,
    health: Mapping[str, int | float],
    *,
    category_live: Mapping[str, list[Entry]] | None = None,
    radio_audio_only_rejected: int = 0,
    radio_audio_only_removed: int = 0,
    discovery_meta: Mapping[str, object] | None = None,
) -> None:
    """Write the v2 schema while keeping the historical helper callable."""
    if category_live is None:
        category_live = {
            "vietnam": [entry for entry in live_entries if is_vietnam(entry)],
            "football": [entry for entry in live_entries if is_target_football(entry)],
            "movies": [entry for entry in live_entries if is_target_movie(entry)],
        }
    category_health = {"vietnam": dict(health), "football": dict(health), "movies": dict(health)}
    path.write_text(
        json.dumps(build_stats(
            archive_entries, category_live, category_health, new_urls, sources_total, sources_ok, sources_failed,
            radio_audio_only_rejected, radio_audio_only_removed, discovery_meta,
        ), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_playlist(path: pathlib.Path, header: str | Iterable[str], entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(header, str):
        header_text = header.rstrip()
    else:
        header_text = "\n".join(str(line).rstrip() for line in header).rstrip()
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write((header_text or "#EXTM3U") + "\n")
        for entry in entries:
            output.write(entry.render())


def _load_state_meta(path: pathlib.Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    meta = data.get("__meta__", {}) if isinstance(data, dict) else {}
    return meta if isinstance(meta, dict) else {}


def _metadata_resolution(e: Entry) -> tuple[int, int] | None:
    text = ascii_text(identity_text(e))
    match = re.search(r"(?<!\d)(\d{3,4})\s*[x×]\s*(\d{3,4})(?!\d)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    for height in (2160, 1440, 1080, 720, 576, 480, 360):
        if re.search(rf"(?<!\d){height}\s*p?(?!\d)", text):
            return (3840 if height == 2160 else 2560 if height == 1440 else 1920 if height == 1080 else 1280, height)
    return None


def _cached_live_category(entries: list[Entry], path: pathlib.Path, require_fhd: bool) -> tuple[list[Entry], dict[str, int | float]]:
    try:
        _header, prior = read_existing(path)
    except OSError:
        prior = []
    allowed = {normalize_url(entry.url) for entry in prior}
    live: list[Entry] = []
    for entry in _unique_entries(entries):
        if normalize_url(entry.url) not in allowed:
            continue
        if require_fhd:
            resolution = _metadata_resolution(entry)
            if not resolution or resolution[0] < FHD_WIDTH or resolution[1] < FHD_HEIGHT:
                continue
        live.append(entry)
    stats = _empty_health(len(_unique_entries(entries)))
    stats["checked"] = len(_unique_entries(entries))
    stats["live"] = len(live)
    stats["dead"] = max(0, int(stats["checked"]) - len(live))
    stats["live_rate_percent"] = round(len(live) / int(stats["checked"]) * 100, 2) if stats["checked"] else 0.0
    return live, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="TV/video append-only archive plus health-checked category playlists")
    ap.add_argument("--sources", default="config/sources.txt")
    ap.add_argument("--source-state", default="")
    ap.add_argument("--output", default="iptvhnd.m3u")
    ap.add_argument("--live-output", default="", help="Deprecated combined Live output; kept as a compatibility union")
    ap.add_argument("--legacy-live-output", default="iptvhnd-live.m3u")
    ap.add_argument("--vietnam-live-output", default="iptvhnd-vietnam-live.m3u")
    ap.add_argument("--football-live-output", default="iptvhnd-football-live.m3u")
    ap.add_argument("--movies-live-output", default="iptvhnd-movies-live.m3u")
    ap.add_argument("--stats", default="stats.json")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--health-timeout", type=int, default=8)
    ap.add_argument("--health-workers", type=int, default=32)
    ap.add_argument("--skip-health", action="store_true", help="Use existing Live outputs; intended for offline fixture runs only")
    args = ap.parse_args()

    source_path = pathlib.Path(args.sources)
    state_path = pathlib.Path(args.source_state) if args.source_state else source_path.parent / "source_state.json"
    output_path = pathlib.Path(args.output)
    legacy_live_path = pathlib.Path(args.live_output or args.legacy_live_output)
    live_paths = {
        "vietnam": pathlib.Path(args.vietnam_live_output),
        "football": pathlib.Path(args.football_live_output),
        "movies": pathlib.Path(args.movies_live_output),
    }
    stats_path = pathlib.Path(args.stats)

    sources = load_sources(source_path)
    old_header, old_entries = read_existing(output_path)
    old_entries, migrated_radio = migrate_archive(old_entries)
    old_entries, _duplicate_archive_entries_removed = dedupe_archive_entries(old_entries)
    archive_seen = {normalize_url(entry.url) for entry in old_entries if normalize_url(entry.url)}
    added: list[Entry] = []
    source_headers: list[list[str]] = []
    sources_ok = sources_failed = 0
    radio_rejected = 0

    print(f"Existing TV/video archive entries: {len(old_entries)} (radio/audio-only migration removed {len(migrated_radio)})")
    print(f"Sources: {len(sources)}")
    for source in sources:
        try:
            source_text = fetch_text(source, timeout=args.timeout)
            header, entries = parse_m3u(source_text)
            source_headers.append(header)
            sources_ok += 1
            archive_kept = archive_new = 0
            for entry in entries:
                if is_definitely_radio(entry):
                    radio_rejected += 1
                    continue
                if not should_keep(entry, source):
                    continue
                archive_kept += 1
                key = normalize_url(entry.url)
                if not key or key in archive_seen:
                    continue
                archive_seen.add(key)
                added.append(entry)
                archive_new += 1
            print(f"OK {source} entries={len(entries)} archive_kept={archive_kept} archive_new={archive_new}")
        except Exception as exc:
            sources_failed += 1
            print(f"WARN {source}: {exc}", file=sys.stderr)

    header = merge_header(old_header, source_headers)
    archive_entries = old_entries + added
    category_entries = _category_entries(archive_entries)

    if args.skip_health:
        category_live: dict[str, list[Entry]] = {}
        category_health: dict[str, dict[str, int | float]] = {}
        for category, entries in category_entries.items():
            path = live_paths[category]
            if not path.exists():
                path = legacy_live_path
            live, health = _cached_live_category(entries, path, require_fhd=category in {"football", "movies"})
            category_live[category] = live
            category_health[category] = health
        audio_only_urls: set[str] = set()
    else:
        unique_category_urls = {normalize_url(entry.url) for entries in category_entries.values() for entry in entries if normalize_url(entry.url)}
        print(f"Health-checking {len(unique_category_urls)} category URLs from iptvhnd.m3u...")
        checked, audio_only_urls = health_check_categories(category_entries, args.health_timeout, args.health_workers)
        category_live = {category: result[0] for category, result in checked.items()}
        category_health = {category: result[1] for category, result in checked.items()}

    # A manifest that proves audio-only is a safe Archive migration too. This
    # is intentionally narrower than metadata suspicion and is counted.
    if audio_only_urls:
        before = len(archive_entries)
        archive_entries = [entry for entry in archive_entries if normalize_url(entry.url) not in audio_only_urls]
        probe_removed = before - len(archive_entries)
        if probe_removed:
            for category in category_live:
                category_live[category] = [entry for entry in category_live[category] if normalize_url(entry.url) not in audio_only_urls]
    else:
        probe_removed = 0

    archive_urls = {normalize_url(entry.url) for entry in archive_entries}
    for category in category_live:
        category_live[category] = [entry for entry in _unique_entries(category_live[category]) if normalize_url(entry.url) in archive_urls]
    live_union = _union_live_entries(category_live)

    write_playlist(output_path, header, archive_entries)
    for category, path in live_paths.items():
        write_playlist(path, header, category_live[category])
    # Keep the old public URL useful for existing users, but make it the clean
    # union of the three new health-checked playlists.
    write_playlist(legacy_live_path, header, live_union)

    stats = build_stats(
        archive_entries,
        category_live,
        category_health,
        len(added),
        len(sources),
        sources_ok,
        sources_failed,
        radio_audio_only_rejected=radio_rejected,
        radio_audio_only_removed=len(migrated_radio) + probe_removed,
        discovery_meta=_load_state_meta(state_path),
    )
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Archive added: {len(added)}")
    print(f"Archive TV/video total: {len(archive_entries)}")
    for category in ("vietnam", "football", "movies"):
        print(f"{category} archive={len(category_entries[category])} live={len(category_live[category])}")
    print(f"Radio/audio-only rejected this cycle: {radio_rejected}; removed from archive: {len(migrated_radio) + probe_removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
