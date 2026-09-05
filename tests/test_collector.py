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
    assert mod.should_keep(e, 'https://iptv-org.github.io/iptv/languages/vie.m3u')


def test_vietnam_local_channel_detected_from_name():
    e = mod.Entry((), '#EXTINF:-1,Đài Phát thanh và Truyền hình Quảng Ninh', (), 'https://x/qn.m3u8')
    assert mod.is_vietnam(e)
    assert mod.locality(e) == 'quang ninh'


def test_vietnam_network_detected_from_tvg_id():
    e = mod.Entry((), '#EXTINF:-1 tvg-id="VTV3.vn",VTV3', (), 'https://x/vtv3.m3u8')
    assert mod.is_vietnam(e)


def test_non_http_stream_is_not_falsely_marked_live():
    e = mod.Entry((), '#EXTINF:-1,Vietnam UDP', (), 'udp://239.1.1.1:1234')
    status, reason = mod.check_http_stream(e, 1)
    assert status == 'unsupported'
    assert reason == 'udp'


def test_request_headers_preserve_vlc_options():
    e = mod.Entry(
        (),
        '#EXTINF:-1,VTV',
        ('#EXTVLCOPT:http-user-agent=CustomAgent', '#EXTVLCOPT:http-referrer=https://ref.example/'),
        'https://x/live.m3u8',
    )
    headers = mod.request_headers_for(e)
    assert headers['User-Agent'] == 'CustomAgent'
    assert headers['Referer'] == 'https://ref.example/'
