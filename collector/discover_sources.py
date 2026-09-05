from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "IPTVHND-SourceDiscovery/1.3"
FAILURE_LIMIT = 3
SEARCH_QUERIES = (
    # Vietnam/community discovery.
    "THVL extension:m3u", "VTC extension:m3u", "VTV extension:m3u", "HTV extension:m3u", "SCTV extension:m3u",
    '"Viet Nam" IPTV extension:m3u', '"Vietnam" IPTV extension:m3u',
    "THVL extension:m3u8", "VTC extension:m3u8", '"Vietnam" IPTV extension:m3u8',
    # International movie/cinema discovery.
    "IPTV movies extension:m3u", "IPTV movie extension:m3u", "IPTV films extension:m3u", "IPTV cinema extension:m3u",
    "movies extension:m3u8", "cinema extension:m3u8",
    # International sports/football discovery.
    "IPTV sports extension:m3u", "IPTV football extension:m3u", "IPTV soccer extension:m3u",
    "sports extension:m3u8", "football extension:m3u8", "soccer extension:m3u8",
)
VIETNAM_HINTS = (
    "thvl", "vtc", "vtv", "htv", "sctv", "vietnam", "viet nam", "vinh long",
    "quang ninh", "can tho", "da nang", "dong nai", "dong thap", "khanh hoa",
)
MOVIE_HINTS = ("movie", "movies", "film", "films", "cinema", "cinemas")
SPORT_HINTS = ("sport", "sports", "football", "soccer", "futbol", "fútbol")


def request_json(url: str, token: str, timeout: int = 20, retries: int = 2) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 403} or attempt >= retries:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 8 * (attempt + 1)
            print(f"WARN GitHub search rate-limited; retrying in {delay}s")
            time.sleep(delay)
    return {}


def fetch_text(url: str, timeout: int = 20) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as resp:
        return resp.read(2_000_000).decode("utf-8-sig", errors="replace")


def load_sources(path: pathlib.Path) -> list[str]:
    if not path.exists(): return []
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in seen:
            seen.add(value); out.append(value)
    return out


def playlist_kind(text: str) -> str | None:
    lower = text.lower()
    if "#extm3u" not in lower or lower.count("#extinf") < 2:
        return None
    if any(h in lower for h in VIETNAM_HINTS): return "vietnam"
    if any(h in lower for h in SPORT_HINTS): return "sports"
    if any(h in lower for h in MOVIE_HINTS): return "movies"
    return None


def looks_like_iptv_playlist(text: str, require_relevant: bool = False) -> bool:
    lower = text.lower()
    base = "#extm3u" in lower and lower.count("#extinf") >= 2
    return base and (not require_relevant or playlist_kind(text) is not None)


def raw_url_from_search_item(item: dict) -> str | None:
    html = item.get("html_url", "")
    marker = "github.com/"
    if marker not in html or "/blob/" not in html:
        return None
    tail = html.split(marker, 1)[1]
    repo_part, blob_part = tail.split("/blob/", 1)
    if "/" not in blob_part:
        return None
    ref, path = blob_part.split("/", 1)
    return f"https://raw.githubusercontent.com/{repo_part}/{ref}/{path}"


def discover_github(token: str, max_results_per_query: int = 30, start_index: int = 0,
                    queries_per_run: int = 8) -> tuple[set[str], int]:
    found: set[str] = set()
    if not token:
        print("WARN GITHUB_TOKEN missing; skipping GitHub source discovery")
        return found, start_index
    total = len(SEARCH_QUERIES)
    count = min(max(1, queries_per_run), total)
    indexes = [(start_index + i) % total for i in range(count)]
    for position, idx in enumerate(indexes):
        query = SEARCH_QUERIES[idx]
        url = "https://api.github.com/search/code?" + urllib.parse.urlencode({"q": query, "per_page": max_results_per_query})
        try:
            payload = request_json(url, token)
        except Exception as exc:
            print(f"WARN search {query!r}: {exc}")
            continue
        for item in payload.get("items", []):
            raw = raw_url_from_search_item(item)
            if raw:
                found.add(raw)
        print(f"Discovery query {query!r}: candidates={len(found)}")
        if position < len(indexes) - 1:
            time.sleep(2)
    return found, (start_index + count) % total


def load_state(path: pathlib.Path) -> dict:
    if not path.exists(): return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")); return data if isinstance(data, dict) else {}
    except Exception: return {}


def validate_sources(existing: list[str], discovered: set[str], state: dict, timeout: int) -> tuple[list[str], dict]:
    candidates = list(existing) + [u for u in sorted(discovered) if u not in existing]
    active, new_state, existing_set = [], {}, set(existing)
    for url in candidates:
        failures = int(state.get(url, {}).get("consecutive_failures", 0))
        try:
            text = fetch_text(url, timeout)
            if not looks_like_iptv_playlist(text, require_relevant=url not in existing_set):
                raise ValueError("not a relevant IPTV playlist")
            failures = 0; active.append(url); ok = True
        except Exception as exc:
            failures += 1; ok = False
            if url in existing_set and failures < FAILURE_LIMIT: active.append(url)
            print(f"WARN source {url}: failure {failures}/{FAILURE_LIMIT}: {exc}")
        new_state[url] = {"consecutive_failures": failures, "last_check_ok": ok}
    return active, new_state


def write_sources(path: pathlib.Path, sources: list[str]) -> None:
    header = (
        "# Auto-maintained public/community IPTV source registry.\n"
        "# Discovery rotates across Vietnam, international movie/cinema, and international sports/football/soccer searches.\n"
        "# Existing sources are removed only after 3 consecutive failed checks.\n"
        "# Content filtering and exact stream-URL dedupe happen later in collector/main.py.\n\n"
    )
    path.write_text(header + "\n".join(sources) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover and maintain public/community IPTV playlist sources")
    ap.add_argument("--sources", default="config/sources.txt"); ap.add_argument("--state", default="config/source_state.json")
    ap.add_argument("--timeout", type=int, default=20); ap.add_argument("--max-results-per-query", type=int, default=30)
    ap.add_argument("--queries-per-run", type=int, default=8)
    args = ap.parse_args(); source_path = pathlib.Path(args.sources); state_path = pathlib.Path(args.state)
    existing = load_sources(source_path); state = load_state(state_path)
    cursor = int(state.get("__meta__", {}).get("query_cursor", 0))
    discovered, next_cursor = discover_github(os.environ.get("GITHUB_TOKEN", ""), args.max_results_per_query, cursor, args.queries_per_run)
    active, new_state = validate_sources(existing, discovered, state, args.timeout)
    new_state["__meta__"] = {"query_cursor": next_cursor}
    write_sources(source_path, active); state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Sources before={len(existing)} discovered_candidates={len(discovered)} active_registry={len(active)} query_cursor={next_cursor}"); return 0


if __name__ == "__main__": raise SystemExit(main())
