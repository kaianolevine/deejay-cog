"""Spotify sync for DJ sets sourced from Google Sheets (CSV pipeline).

Updates the radio playlist and per-set playlists from sheet track rows, and
pushes a full Spotify playlist snapshot to the Kaiano API when configured.
"""

from __future__ import annotations

import os
from typing import Any

from mini_app_polis import logger as logger_mod
from mini_app_polis.spotify import SpotifyAPI

log = logger_mod.get_logger()

# Env-driven config (mini_app_polis loads dotenv when config is first imported).
SPOTIFY_RADIO_PLAYLIST_ID = os.getenv("SPOTIFY_RADIO_PLAYLIST_ID")

DEFAULT_PLAYLIST_DESCRIPTION = (
    "Generated automatically by Deejay Marvel Automation Tools. "
    "Spreadsheets of history and song-not-found logs can be found at "
    "www.kaianolevine.com/dj-marvel"
)


def _first_attr(obj: Any, names: list[str]) -> Any:
    """Return the first existing attribute value on obj from a list of names."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _call_first(sp: Any, method_names: list[str], *args: Any, **kwargs: Any) -> Any:
    """Call the first method that exists on sp; return its result."""
    for name in method_names:
        fn = getattr(sp, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    raise AttributeError(f"None of these methods exist on SpotifyAPI: {method_names}")


def _extract_external_url(playlist: dict) -> str:
    # Spotify returns external_urls: {spotify: 'https://open.spotify.com/playlist/...'}
    external = playlist.get("external_urls") or {}
    if isinstance(external, dict):
        return external.get("spotify", "") or ""
    return ""


def _normalize_playlist_item(p: dict) -> dict:
    """Normalize a Spotify playlist object into a stable JSON-friendly dict."""
    owner = p.get("owner") or {}
    tracks = p.get("tracks") or {}

    return {
        "id": p.get("id", ""),
        "name": p.get("name", ""),
        "url": _extract_external_url(p),
        "uri": p.get("uri", ""),
        "type": p.get("type", "playlist"),
        "public": p.get("public"),
        "collaborative": p.get("collaborative"),
        "snapshot_id": p.get("snapshot_id", ""),
        "tracks_total": tracks.get("total"),
        "owner": {
            "id": owner.get("id", ""),
            "display_name": owner.get("display_name", ""),
        },
    }


def fetch_all_playlists(sp: Any) -> list[dict]:
    """Fetch all playlists visible to the account.

    This is intentionally defensive because SpotifyAPI wrappers differ.
    We try a handful of common method names; if none exist, we log and return [].

    Expected return shape is a list of raw Spotify playlist dicts.
    """
    try:
        return _call_first(
            sp,
            [
                "get_all_playlists",
                "get_user_playlists",
                "list_playlists",
                "get_playlists",
                "fetch_playlists",
            ],
        )
    except Exception:
        pass

    client = _first_attr(sp, ["client", "spotify", "sp", "_client", "_sp"])
    if client is None:
        return []

    fn = getattr(client, "current_user_playlists", None)
    if not callable(fn):
        return []

    items: list[dict] = []
    limit = 50
    offset = 0

    while True:
        page = fn(limit=limit, offset=offset)
        if not isinstance(page, dict):
            break

        page_items = page.get("items") or []
        if isinstance(page_items, list):
            items.extend(page_items)

        if page.get("next"):
            offset += limit
            continue

        break

    return items


def push_playlists_to_api(sp: Any) -> int | None:
    """
    Fetch all playlists from the Spotify account and push a full snapshot
    to POST /v1/spotify/playlists via KaianoApiClient.

    The API upserts all playlists using snapshot_id to skip unchanged rows.
    Returns the number of playlists upserted, or None if the API call was
    skipped (KAIANO_API_BASE_URL not set) or failed.

    Skips gracefully if KAIANO_API_BASE_URL is not set.
    """
    if not os.getenv("KAIANO_API_BASE_URL"):
        log.warning(
            "KAIANO_API_BASE_URL not set — skipping Spotify playlist push to API",
        )
        return None

    try:
        from mini_app_polis.api import KaianoApiClient  # type: ignore
        from mini_app_polis.api.errors import KaianoApiError  # type: ignore
    except Exception as e:
        log.error("Kaiano API client not available; skipping playlist push: %s", e)
        return None

    raw_playlists = fetch_all_playlists(sp)
    normalized = [
        _normalize_playlist_item(p) for p in raw_playlists if isinstance(p, dict)
    ]

    payload = {
        "playlists": [
            {
                "id": p["id"],
                "name": p["name"],
                "url": p["url"],
                "uri": p["uri"],
                "type": p["type"],
                "public": p["public"] if p["public"] is not None else True,
                "collaborative": p["collaborative"]
                if p["collaborative"] is not None
                else False,
                "snapshot_id": p["snapshot_id"],
                "tracks_total": p["tracks_total"]
                if p["tracks_total"] is not None
                else 0,
                "owner_id": p["owner"]["id"],
                "owner_name": p["owner"].get("display_name"),
            }
            for p in normalized
            if p.get("id") and p.get("name")
        ]
    }

    try:
        client = KaianoApiClient.from_env()
        response = client.post("/v1/spotify/playlists", payload)
    except KaianoApiError as e:
        log.error("Spotify playlist push to API failed: %s", e)
        return None

    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        log.error("Spotify playlist API response missing data: %s", response)
        return None

    upserted = data.get("upserted")
    unchanged = data.get("unchanged", 0)
    if upserted is None:
        log.error("Spotify playlist API response missing upserted count: %s", response)
        return None

    log.info(
        "✅ Spotify playlists pushed to API: %s upserted, %s unchanged",
        upserted,
        unchanged,
    )
    return int(upserted)


def update_spotify_radio_playlist(
    sp: SpotifyAPI, playlist_id: str | None, found_uris: list[str]
) -> None:
    """Append tracks to the main radio playlist and trim."""
    if not playlist_id or not found_uris:
        return

    try:
        sp.add_tracks_to_specific_playlist(playlist_id, found_uris)
        sp.trim_playlist_to_limit()
    except Exception as e:
        log.error("Error updating Spotify radio playlist: %s", e, exc_info=True)


def create_spotify_playlist_for_file(
    sp: SpotifyAPI, set_name: str, found_uris: list[str]
) -> str | None:
    """Create or replace a per-set Spotify playlist.

    If a playlist with the given name already exists, it is cleared and
    repopulated with ``found_uris``. Otherwise a new playlist is created.
    """
    if not found_uris:
        return None

    try:
        existing = sp.find_playlist_by_name(set_name)
        if existing:
            playlist_id = existing["id"]
            sp.clear_playlist(playlist_id)
            sp.add_tracks_to_specific_playlist(playlist_id, found_uris)
            return playlist_id

        playlist_id = sp.create_playlist(set_name, DEFAULT_PLAYLIST_DESCRIPTION)
        if not playlist_id:
            return None

        unique_uris = list(dict.fromkeys(found_uris))
        sp.add_tracks_to_specific_playlist(playlist_id, unique_uris)
        return playlist_id

    except Exception as e:
        log.error(
            "Failed creating/updating playlist '%s': %s",
            set_name,
            e,
            exc_info=True,
        )
        return None


def get_spotify_client() -> SpotifyAPI | None:
    """Return SpotifyAPI.from_env() or None if credentials are missing."""
    if not os.getenv("SPOTIPY_CLIENT_ID") or not os.getenv("SPOTIPY_REFRESH_TOKEN"):
        log.warning(
            "SPOTIPY_CLIENT_ID or SPOTIPY_REFRESH_TOKEN not set; "
            "Spotify client unavailable.",
        )
        return None
    try:
        return SpotifyAPI.from_env()
    except Exception as e:
        log.error("Failed to initialize Spotify client: %s", e, exc_info=True)
        return None


def sync_set_to_spotify(
    sp: SpotifyAPI,
    set_name: str,
    tracks: list[dict],
) -> str | None:
    """Search Spotify for each track and update playlists.

    Returns the per-set playlist ID if one was created/updated, else None.
    Idempotent — existing playlists are found by name before creating.
    Never raises.
    """
    try:
        found_uris: list[str] = []
        matched: list[tuple[str, str]] = []
        not_found = 0

        for t in tracks:
            artist = str(t.get("artist") or "").strip()
            title = str(t.get("title") or "").strip()
            if not artist or not title:
                continue
            uri = sp.search_track(artist, title)
            if uri:
                found_uris.append(uri)
                matched.append((artist, title))
            else:
                not_found += 1

        log.info(
            "%s: %d found on Spotify, %d not found",
            set_name,
            len(matched),
            not_found,
        )

        update_spotify_radio_playlist(sp, SPOTIFY_RADIO_PLAYLIST_ID, found_uris)
        return create_spotify_playlist_for_file(sp, set_name, found_uris)
    except Exception as e:
        log.error("sync_set_to_spotify failed: %s", e, exc_info=True)
        return None
