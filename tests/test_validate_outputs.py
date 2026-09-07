import importlib.util
import json
import sys
from pathlib import Path


repo = Path(__file__).parents[1]
collector_dir = repo / "collector"
sys.path.insert(0, str(collector_dir))

main_spec = importlib.util.spec_from_file_location("main", collector_dir / "main.py")
main_mod = importlib.util.module_from_spec(main_spec)
sys.modules["main"] = main_mod
main_spec.loader.exec_module(main_mod)

validate_spec = importlib.util.spec_from_file_location("validate_outputs", collector_dir / "validate_outputs.py")
validate_mod = importlib.util.module_from_spec(validate_spec)
sys.modules[validate_spec.name] = validate_mod
validate_spec.loader.exec_module(validate_mod)


def test_validator_accepts_clean_archive_to_live_fixture(tmp_path):
    archive = [
        main_mod.Entry((), '#EXTINF:-1 tvg-id="VTV1.vn",VTV1', (), "https://x/vn.m3u8"),
        main_mod.Entry((), '#EXTINF:-1,Premier League', (), "https://x/football.m3u8"),
        main_mod.Entry((), '#EXTINF:-1,HBO', (), "https://x/movies.m3u8"),
    ]
    category_live = {"vietnam": [archive[0]], "football": [archive[1]], "movies": [archive[2]]}
    health = {}
    for category in category_live:
        value = main_mod._empty_health(1)
        value["checked"] = 1
        value["live"] = 1
        value["live_rate_percent"] = 100.0
        health[category] = value

    archive_path = tmp_path / "iptvhnd.m3u"
    vietnam_path = tmp_path / "iptvhnd-vietnam-live.m3u"
    football_path = tmp_path / "iptvhnd-football-live.m3u"
    movies_path = tmp_path / "iptvhnd-movies-live.m3u"
    stats_path = tmp_path / "stats.json"
    for path, entries in (
        (archive_path, archive),
        (vietnam_path, category_live["vietnam"]),
        (football_path, category_live["football"]),
        (movies_path, category_live["movies"]),
    ):
        main_mod.write_playlist(path, "#EXTM3U", entries)
    stats_path.write_text(json.dumps(main_mod.build_stats(archive, category_live, health, 0, 1, 1, 0)), encoding="utf-8")

    assert validate_mod.validate(archive_path, vietnam_path, football_path, movies_path, stats_path) == {
        "archive": 3, "vietnam": 1, "football": 1, "movies": 1,
    }
