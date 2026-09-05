from __future__ import annotations

import argparse
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "IPTVHND-SourceDiscovery/1.0"
FAILURE_LIMIT = 3
SEARCH_QUERIES = (
    "THVL extension:m3u",
    "VTC extension:m3u",
    "VTV extension:m3u",
    "HTV extension:m3u",
    "SCTV extension:m3u",
    '"Viet Nam" IPTV extension:m3u',
    '"Vietnam" IPTV extension:m3u',
    "THVL extension:m3u8",
    "VTC extension:m3u8",
    '"Vietnam" IPTV extension:m3u8',
)
VIETNAM_HINTS = (
    "thvl", "vtc", "vtv", "htv", "sctv", "vietnam", "viet nam", "vinh long",
    "quang ninh", "can tho", "da nang", "dong nai", "dong thap", "khanh hoa",
)


def request_json(url: str, token: str, timeout: int = 20) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2_000_000).decode("utf-8-sig", errors="replace")


def load_sources(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def looks_like_iptv_playlist(text: str) -> bool:
    lower = text.lower()
    return "#extm3u" in lower and lower.count("#extinf") >= 2 and any(h in lower for h in VIETNAM_HINTS)


def discover_github(token: str, max_results_per_query: int = 50) -> set[str]:
    found: set[str] = set()
    if not token:
        print("WARN GITHUB_TOKEN missing; skipping GitHub source discovery")
        return found
    for query in SEARCH_QUERIES:
        url = "https://api.github.com/search/code?" + urllib.parse.urlencode({"q": query, "per_page": max_results_per_query})
        try:
            payload = request_json(url, token)
        except Exception as exc:
            print(f"WARN search {query!r}: {exc}")
            continue
        for item in payload.get("items", []):
            api_url = item.get("url")
            if not api_url:
                continue
            try:
                meta = request_json(api_url, token)
            except Exception:
                continue
            download_url = meta.get("download_url")
            if download_url:
                found.add(download_url)
        print(f"Discovery query {query!r}: candidates={len(found)}")
    return found


def load_state(path: pathlib.Path) -> dict[str, dict[str, int | bool]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def validate_sources(existing: list[str], discovered: set[str], state: dict, timeout: int) -> tuple[list[str], dict]:
    candidates = list(existing)
    for url in sorted(discovered):
        if url not in candidates:
            candidates.append(url)

    active: list[str] = []
    new_state: dict[str, dict[str, int | bool]] = {}
    existing_set = set(existing)
    for url in candidates:
        prior = state.get(url, {})
        failures = int(prior.get("consecutive_failures", 0))
        try:
            text = fetch_text(url, timeout)
            if not looks_like_iptv_playlist(text):
                raise ValueError("not an IPTV playlist with Vietnam hints")
            failures = 0
            active.append(url)
            ok = True
        except Exception as exc:
            failures += 1
            ok = False
            # Existing sources are removed only after repeated failures. Newly discovered
            # invalid/unreachable candidates are never admitted to the registry.
            if url in existing_set and failures < FAILURE_LIMIT:
                active.append(url)
            print(f"WARN source {url}: failure {failures}/{FAILURE_LIMIT}: {exc}")
        new_state[url] = {"consecutive_failures": failures, "last_check_ok": ok}

    return active, new_state


def write_sources(path: pathlib.Path, sources: list[str]) -> None:
    header = (
        "# Auto-maintained public/community IPTV source registry.\n"
        "# Discovery appends reachable Vietnam-relevant playlists; an existing source is removed only after 3 consecutive failed checks.\n"
        "# Stream filtering and exact URL dedupe happen later in collector/main.py.\n\n"
    )
    path.write_text(header + "\n".join(sources) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover and maintain public/community IPTV playlist sources")
    ap.add_argument("--sources", default="config/sources.txt")
    ap.add_argument("--state", default="config/source_state.json")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--max-results-per-query", type=int, default=50)
    args = ap.parse_args()

    source_path = pathlib.Path(args.sources)
    state_path = pathlib.Path(args.state)
    existing = load_sources(source_path)
    state = load_state(state_path)
    discovered = discover_github(os.environ.get("GITHUB_TOKEN", ""), args.max_results_per_query)
    active, new_state = validate_sources(existing, discovered, state, args.timeout)
    write_sources(source_path, active)
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Sources before={len(existing)} discovered_candidates={len(discovered)} active_registry={len(active)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
