import importlib.util
import sys
from pathlib import Path

module_path = Path(__file__).parents[1] / "collector" / "main.py"
spec = importlib.util.spec_from_file_location("collector_main", module_path)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_parse_preserves_metadata_and_directives():
    text = '''#EXTM3U url-tvg="https://epg.example/guide.xml"
#EXTINF:-1 tvg-id="VTV1.vn" tvg-logo="https://logo/vtv1.png" group-title="Vietnam",VTV1
#EXTVLCOPT:http-referrer=https://example.com/
https://stream.example/vtv1.m3u8
'''
    header, entries = mod.parse_m3u(text)
    assert header[0].startswith("#EXTM3U")
    assert entries[0].extinf.endswith(",VTV1")
    assert entries[0].post_lines == ("#EXTVLCOPT:http-referrer=https://example.com/",)
    assert entries[0].url == "https://stream.example/vtv1.m3u8"


def test_url_dedupe_only():
    a = mod.Entry((), '#EXTINF:-1 group-title="Vietnam",Same name', (), 'https://a/1.m3u8')
    b = mod.Entry((), '#EXTINF:-1 group-title="Vietnam",Same name', (), 'https://a/2.m3u8')
    assert mod.normalize_url(a.url) != mod.normalize_url(b.url)


def test_category_sources_kept():
    e = mod.Entry((), '#EXTINF:-1,Anything', (), 'https://x/live.m3u8')
    assert mod.should_keep(e, 'https://iptv-org.github.io/iptv/categories/sports.m3u')
    assert mod.should_keep(e, 'https://iptv-org.github.io/iptv/categories/movies.m3u')


def test_vietnam_source_keeps_all_entries():
    e = mod.Entry((), '#EXTINF:-1 tvg-logo="https://logo/x.png",Local channel', (), 'https://x/live.m3u8')
    assert mod.should_keep(e, 'https://iptv-org.github.io/iptv/countries/vn.m3u')
