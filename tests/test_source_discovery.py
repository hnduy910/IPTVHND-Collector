import importlib.util
import sys
from pathlib import Path

module_path = Path(__file__).parents[1] / "collector" / "discover_sources.py"
spec = importlib.util.spec_from_file_location("source_discovery", module_path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_playlist_requires_m3u_extinf_and_vietnam_hint():
    assert mod.looks_like_iptv_playlist("#EXTM3U\n#EXTINF:-1,THVL1\na\n#EXTINF:-1,VTV1\nb\n")
    assert not mod.looks_like_iptv_playlist("#EXTM3U\n#EXTINF:-1,News\na\n")


def test_existing_source_survives_transient_failures(monkeypatch):
    def fail(_url, _timeout):
        raise OSError("temporary")

    monkeypatch.setattr(mod, "fetch_text", fail)
    url = "https://example.com/list.m3u"
    active, state = mod.validate_sources([url], set(), {}, 1)
    assert url in active
    assert state[url]["consecutive_failures"] == 1


def test_existing_source_removed_after_three_failures(monkeypatch):
    def fail(_url, _timeout):
        raise OSError("gone")

    monkeypatch.setattr(mod, "fetch_text", fail)
    url = "https://example.com/list.m3u"
    active, state = mod.validate_sources(
        [url], set(), {url: {"consecutive_failures": 2, "last_check_ok": False}}, 1
    )
    assert url not in active
    assert state[url]["consecutive_failures"] == 3


def test_new_bad_candidate_is_not_admitted(monkeypatch):
    monkeypatch.setattr(mod, "fetch_text", lambda _url, _timeout: "not a playlist")
    url = "https://example.com/bad.m3u"
    active, _ = mod.validate_sources([], {url}, {}, 1)
    assert url not in active
