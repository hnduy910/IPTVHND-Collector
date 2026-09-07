import importlib.util
import json
import sys
from pathlib import Path

import pytest


module_path = Path(__file__).parents[1] / "collector" / "main.py"
spec = importlib.util.spec_from_file_location("collector_main", module_path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def entry(name, url="https://stream.example/live.m3u8", group=""):
    group_attr = f' group-title="{group}"' if group else ""
    return mod.Entry((), f'#EXTINF:-1{group_attr},{name}', (), url)


def test_parse_preserves_header_metadata_and_directives():
    text = '''#EXTM3U url-tvg="https://epg.example/guide.xml"
#EXT-X-VERSION:3
#EXTINF:-1 tvg-id="VTV1.vn" tvg-logo="https://logo/vtv1.png" group-title="Vietnam",VTV1
#EXTVLCOPT:http-referrer=https://example.com/
https://stream.example/vtv1.m3u8
'''
    header, entries = mod.parse_m3u(text)
    assert header == ["#EXTM3U url-tvg=\"https://epg.example/guide.xml\"", "#EXT-X-VERSION:3"]
    assert entries[0].extinf.endswith(",VTV1")
    assert entries[0].post_lines == ("#EXTVLCOPT:http-referrer=https://example.com/",)
    assert entries[0].url == "https://stream.example/vtv1.m3u8"


def test_exact_url_dedupe_only_and_same_channel_different_urls_are_kept():
    a = entry("Same name", "https://a/1.m3u8")
    b = entry("Same name", "https://a/2.m3u8")
    assert mod.normalize_url(a.url) != mod.normalize_url(b.url)
    assert mod._unique_entries([a, b, a]) == [a, b]


def test_radio_metadata_is_rejected_but_music_tv_is_not():
    assert mod.is_definitely_radio(entry("VOV Radio", "https://a/radio.mp3", "Radio"))
    assert mod.is_definitely_radio(entry("FM 99.9", "https://a/fm.aac"))
    assert mod.is_definitely_radio(entry("Podcast News", "https://a/podcast.m3u8"))
    assert not mod.is_definitely_radio(entry("MTV Music TV", "https://a/music.m3u8", "Music"))
    assert not mod.is_definitely_radio(entry("Music One", "https://a/music-tv.m3u8", "Music"))


def test_vietnam_tv_is_kept_at_every_resolution():
    for label in ("480p", "720p", "1080p", "4K"):
        channel = entry(f"VTV1 {label}", f"https://a/{label}.m3u8")
        assert mod.should_keep(channel, "https://iptv-org.github.io/iptv/countries/vn.m3u")
        assert mod.is_vietnam(channel, "https://iptv-org.github.io/iptv/countries/vn.m3u")


def test_vietnam_local_channel_outside_provider_reference_is_kept():
    channel = entry("Đài Phát thanh và Truyền hình Quảng Ninh", "https://x/qn.m3u8")
    assert mod.is_vietnam(channel)
    assert mod.locality(channel) == "quang ninh"
    assert mod.vietnam_family(channel) == "Vietnam local"


def test_vietnam_network_detected_from_tvg_id():
    channel = mod.Entry((), '#EXTINF:-1 tvg-id="VTV3.vn",VTV3', (), "https://x/vtv3.m3u8")
    assert mod.is_vietnam(channel)


def test_generic_source_categories_do_not_admit_generic_sports_or_movies():
    assert not mod.should_keep(entry("Sports One"), "https://iptv-org.github.io/iptv/categories/sports.m3u")
    assert not mod.should_keep(entry("Anything"), "https://iptv-org.github.io/iptv/categories/movies.m3u")
    assert mod.should_keep(entry("Premier League"), "https://iptv-org.github.io/iptv/categories/sports.m3u")
    assert mod.should_keep(entry("HBO"), "https://iptv-org.github.io/iptv/categories/movies.m3u")


def test_football_target_and_non_football_sports():
    assert mod.is_target_football(entry("Premier League HD"))
    assert mod.is_target_football(entry("UEFA Champions League"))
    assert mod.is_target_football(entry("Football News"))
    assert not mod.is_target_football(entry("NBA TV Sports"))
    assert not mod.is_target_football(entry("Tennis Channel"))


def test_movie_reference_is_curated():
    assert mod.is_target_movie(entry("HBO HD"))
    assert mod.is_target_movie(entry("ON Phim Việt"))
    assert mod.is_target_movie(entry("Cinema World"))
    assert not mod.is_target_movie(entry("Random Movie Playlist"))
    assert not mod.is_target_movie(entry("Netflix"))
    assert not mod.should_keep(entry("Netflix DRM", "https://example.com/netflix.mpd"), "https://example.com/movies.m3u")


def test_hls_master_resolution_and_audio_detection():
    master = '''#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
1080/index.m3u8
'''
    probe = mod._probe_hls(master, "application/vnd.apple.mpegurl", "https://x/master.m3u8")
    assert probe.status == "live"
    assert (1920, 1080) in probe.resolutions
    assert mod._classify_probe(probe, require_fhd=True)[0] == "live"

    audio = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Radio",DEFAULT=YES,URI="audio.m3u8"
'''
    audio_probe = mod._probe_hls(audio, "application/vnd.apple.mpegurl", "https://x/audio.m3u8")
    assert audio_probe.status == "audio_only"


def test_resolution_policy_for_football_and_movies():
    low = mod.StreamProbe("live", "hls_master", resolutions=((1280, 720),), video=True)
    high = mod.StreamProbe("live", "hls_master", resolutions=((1280, 720), (1920, 1080)), video=True)
    unknown = mod.StreamProbe("live", "hls_media", video=True)
    assert mod._classify_probe(low, require_fhd=True)[0] == "resolution_rejected"
    assert mod._classify_probe(high, require_fhd=True)[0] == "live"
    assert mod._classify_probe(unknown, require_fhd=True)[0] == "resolution_unknown"
    assert mod._classify_probe(low, require_fhd=False)[0] == "live"


def test_non_http_stream_is_not_falsely_marked_live():
    status, reason = mod.check_http_stream(entry("Vietnam UDP", "udp://239.1.1.1:1234"), 1)
    assert status == "unsupported"
    assert reason == "udp"


def test_request_headers_preserve_vlc_options_and_attributes():
    stream = mod.Entry(
        (),
        '#EXTINF:-1 http-user-agent="AttrAgent" http-referrer="https://attr.example/",VTV',
        ("#EXTVLCOPT:http-user-agent=CustomAgent", "#EXTVLCOPT:http-referrer=https://ref.example/"),
        "https://x/live.m3u8",
    )
    headers = mod.request_headers_for(stream)
    assert headers["User-Agent"] == "CustomAgent"
    assert headers["Referer"] == "https://ref.example/"


def test_archive_radio_migration_is_conservative():
    radio = entry("VOV Radio", "https://a/radio.mp3", "Radio")
    tv = entry("VOV TV", "https://a/tv.m3u8", "News")
    kept, removed = mod.migrate_archive([radio, tv])
    assert kept == [tv]
    assert removed == [radio]


def test_category_health_requires_live_archive_membership_and_fhd(monkeypatch):
    vn = entry("VTV1", "https://a/vn.m3u8")
    football_low = entry("Premier League 720p", "https://a/low.m3u8")
    football_high = entry("Premier League 1080p", "https://a/high.m3u8")
    movie_audio = entry("HBO", "https://a/audio.m3u8")
    probes = {
        vn.url: mod.StreamProbe("live", "hls_media", video=True),
        football_low.url: mod.StreamProbe("live", "hls_master", resolutions=((1280, 720),), video=True),
        football_high.url: mod.StreamProbe("live", "hls_master", resolutions=((1920, 1080),), video=True),
        movie_audio.url: mod.StreamProbe("audio_only", "audio_only_hls", video=False, audio=True),
    }
    monkeypatch.setattr(mod, "probe_http_stream", lambda item, _timeout: probes[item.url])
    results, audio_urls = mod.health_check_categories(
        {"vietnam": [vn], "football": [football_low, football_high], "movies": [movie_audio]}, 1, 2
    )
    assert results["vietnam"][0] == [vn]
    assert results["football"][0] == [football_high]
    assert results["football"][1]["resolution_rejected"] == 1
    assert results["movies"][1]["audio_only_rejected"] == 1
    assert movie_audio.url in audio_urls


def test_build_stats_has_three_category_health_blocks():
    archive = [entry("VTV1"), entry("Premier League"), entry("HBO")]
    live = {"vietnam": [archive[0]], "football": [], "movies": []}
    health = {category: mod._empty_health(1) for category in ("vietnam", "football", "movies")}
    stats = mod.build_stats(archive, live, health, 2, 3, 3, 0, 1, 2, {"newly_discovered_sources": 4})
    assert stats["schema_version"] == 2
    assert stats["sources"]["newly_discovered"] == 4
    assert stats["archive"]["radio_audio_only_rejected"] == 1
    assert set(stats["health"]) == {"vietnam", "football", "movies"}
    for key in ("VTV", "HTV/HTVC", "THVL", "VTC", "SCTV", "VTVcab/ON", "Vietnam local", "Vietnam other"):
        assert key in stats["vietnam"]["groups"]


def test_main_builds_archive_and_three_live_outputs_from_health_checked_archive(tmp_path, monkeypatch):
    source_file = tmp_path / "sources.txt"
    source_file.write_text("https://example.test/source.m3u\n", encoding="utf-8")
    archive_file = tmp_path / "iptvhnd.m3u"
    archive_file.write_text(
        '#EXTM3U\n'
        '#EXTINF:-1 group-title="Radio",VOV Radio\nhttps://old/radio.mp3\n'
        '#EXTINF:-1,VTV1 old\nhttps://old/vtv.m3u8\n'
        '#EXTINF:-1,VTV1 duplicate\nhttps://old/vtv.m3u8\n',
        encoding="utf-8",
    )
    source_text = (
        "#EXTM3U\n"
        '#EXTINF:-1,VTV1 SD\nhttps://new/vtv.m3u8\n'
        '#EXTINF:-1,Premier League 1080p\nhttps://new/football.m3u8\n'
        '#EXTINF:-1,HBO HD\nhttps://new/movies.m3u8\n'
        '#EXTINF:-1,NBA Sports\nhttps://new/nba.m3u8\n'
        '#EXTINF:-1,Random Movie Playlist\nhttps://new/random.m3u8\n'
        '#EXTINF:-1 group-title="Radio",VOV Radio\nhttps://new/radio.mp3\n'
    )
    probes = {
        "https://old/vtv.m3u8": mod.StreamProbe("live", "hls_media", video=True),
        "https://new/vtv.m3u8": mod.StreamProbe("live", "hls_media", video=True),
        "https://new/football.m3u8": mod.StreamProbe("live", "hls_master", resolutions=((1920, 1080),), video=True),
        "https://new/movies.m3u8": mod.StreamProbe("live", "hls_master", resolutions=((3840, 2160),), video=True),
    }
    monkeypatch.setattr(mod, "fetch_text", lambda url, timeout=30: source_text if url.endswith("source.m3u") else "")
    monkeypatch.setattr(mod, "probe_http_stream", lambda item, _timeout: probes[item.url])
    monkeypatch.setattr(sys, "argv", [
        "collector/main.py", "--sources", str(source_file), "--output", str(archive_file),
        "--source-state", str(tmp_path / "source_state.json"), "--vietnam-live-output", str(tmp_path / "vn.m3u"),
        "--football-live-output", str(tmp_path / "football.m3u"), "--movies-live-output", str(tmp_path / "movies.m3u"),
        "--legacy-live-output", str(tmp_path / "legacy.m3u"), "--stats", str(tmp_path / "stats.json"),
    ])

    assert mod.main() == 0
    archive_urls = {entry.url for entry in mod.parse_m3u(archive_file.read_text(encoding="utf-8"))[1]}
    assert "https://old/radio.mp3" not in archive_urls
    assert len(archive_urls) == 4
    assert "https://new/vtv.m3u8" in {entry.url for entry in mod.parse_m3u((tmp_path / "vn.m3u").read_text(encoding="utf-8"))[1]}
    assert {entry.url for entry in mod.parse_m3u((tmp_path / "football.m3u").read_text(encoding="utf-8"))[1]} == {"https://new/football.m3u8"}
    assert {entry.url for entry in mod.parse_m3u((tmp_path / "movies.m3u").read_text(encoding="utf-8"))[1]} == {"https://new/movies.m3u8"}
    stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert stats["archive"]["radio_audio_only_removed"] == 1
    assert stats["football"]["live"] == 1
    assert stats["movies"]["live"] == 1


def test_reference_file_is_public_name_data_only():
    reference = json.loads((Path(__file__).parents[1] / "config" / "channel_reference.json").read_text(encoding="utf-8"))
    assert "MyTV" in reference["vietnam_providers"]
    assert "HBO" in reference["movie_families"]
    assert reference["safety"]["do_not_bypass_drm"] is True
