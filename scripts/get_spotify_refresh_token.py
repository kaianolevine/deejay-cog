"""
One-time local utility to obtain a Spotify OAuth refresh token.

Run this once locally, copy the printed refresh token, and store it as
the SPOTIPY_REFRESH_TOKEN GitHub Actions secret.

Usage:
    uv run python scripts/get_spotify_refresh_token.py

Prerequisites:
    - SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI
      must be set in your local .env file.
    - The redirect URI must be registered in your Spotify Developer Dashboard
      app settings (https://developer.spotify.com/dashboard).
"""

import os

from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

client_id = os.getenv("SPOTIPY_CLIENT_ID")
client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

if not all([client_id, client_secret]):
    print(
        "❌ SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET must be set in your .env file."
    )
    raise SystemExit(1)

sp_oauth = SpotifyOAuth(
    client_id=client_id,
    client_secret=client_secret,
    redirect_uri=redirect_uri,
    scope="playlist-modify-public playlist-modify-private",
    open_browser=True,
)

print(f"Opening browser for Spotify authorization (redirect URI: {redirect_uri}) ...")
token_info = sp_oauth.get_access_token(as_dict=True)

if token_info and token_info.get("refresh_token"):
    print("\n✅ REFRESH TOKEN:", token_info["refresh_token"])
    print("\nStore this as the SPOTIPY_REFRESH_TOKEN GitHub Actions secret.")
else:
    print("❌ Failed to retrieve token. Check your credentials and redirect URI.")
    raise SystemExit(1)
