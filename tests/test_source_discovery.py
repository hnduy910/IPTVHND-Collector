import importlib.util
import sys
from pathlib import Path

module_path = Path(__file__).parents[1] / "collector" / "discover_sources.py"
spec = importlib.util.spec_from_file_location("source_discovery", module_path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def playlist(*names):
    return "#EXTM3U\n" + "".join(f"#EXTINF:-1,{name}\nhttps://x/{i}.m3u8\n" for i, name in enumerate(names))


def test_relevant_playlist_kinds():
    assert mod.playlist_kind(playlist("THVL1", "VTV1")) == "vietnam"
    assert mod.playlist_kind(playlist("Cinema One", "Movie Plus")) == "movies"
    assert mod.playlist_kind(playlist("Sports One", "Football Live")) == "sports"
    assert mod.playlist_kind(playlist("Generic One", "Generic Two")) is None


def test_discovered_movie_and_sports_sources_are_admitted(monkeypatch):
    movie = "https://example.com/movies.m3u"
    sports = "https://example.com/sports.m3u"
    data = {movie: playlist("Movie One", "Cinema Two"), sports: playlist("Sports One", "Football Two")}
    monkeypatch.setattr(mod, "fetch_text", lambda url, _timeout: data[url])
    active, _ = mod.validate_sources([], {movie, sports}, {}, 1)
    assert movie in active
    assert sports in active


def test_existing_source_survives_transient_failures(monkeypatch):
    def fail(_url, _timeout): raise OSError("temporary")
    monkeypatch.setattr(mod, "fetch_text", fail)
    url = "https://example.com/list.m3u"
    active, state = mod.validate_sources([url], set(), {}, 1)
    assert url in active
    assert state[url]["consecutive_failures"] == 1


def test_existing_source_removed_after_three_failures(monkeypatch):
    def fail(_url, _timeout): raise OSError("gone")
    monkeypatch.setattr(mod, "fetch_text", fail)
    url = "https://example.com/list.m3u"
    active, state = mod.validate_sources([url], set(), {url: {"consecutive_failures": 2, "last_check_ok": False}}, 1)
    assert url not in active
    assert state[url]["consecutive_failures"] == 3


def test_new_irrelevant_candidate_is_not_admitted(monkeypatch):
    monkeypatch.setattr(mod, "fetch_text", lambda _url, _timeout: playlist("Generic One", "Generic Two"))
    url = "https://example.com/generic.m3u"
    active, _ = mod.validate_sources([], {url}, {}, 1)
    assert url not in active
