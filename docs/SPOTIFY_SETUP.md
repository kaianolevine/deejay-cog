# Spotify setup

This document covers the one-time setup needed to enable Spotify sync in
this pipeline.

---

## 1. Create a Spotify Developer app

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and log in with the Spotify account you want to use.
2. Click **Create app**.
3. Fill in a name and description (anything is fine).
4. Set the **Redirect URI** to `http://127.0.0.1:8888/callback` and save.
5. From the app settings, copy your **Client ID** and **Client Secret**.

---

## 2. Set up your local `.env`

Add the following to your local `.env` (do not commit this file):

```env
SPOTIPY_CLIENT_ID=your-client-id
SPOTIPY_CLIENT_SECRET=your-client-secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

---

## 3. Get a refresh token

Run the helper script:

```bash
uv run python scripts/get_spotify_refresh_token.py
```

This opens a browser window asking you to log in and authorize the app.
After authorizing, you will be redirected to `http://127.0.0.1:8888/callback`
— the page will likely show an error or be blank, which is expected. Copy
the full URL from the browser address bar and paste it into the terminal
when prompted.

The script will print:

```
✅ REFRESH TOKEN: AQD...
```

Copy that token — you only need to do this once.

---

## 4. Add secrets and variables to GitHub Actions

In your GitHub repo settings, add the following:

**Secrets** (Settings → Secrets and variables → Actions → Secrets):

| Secret | Value |
|--------|-------|
| `SPOTIPY_CLIENT_ID` | Your Spotify app client ID |
| `SPOTIPY_CLIENT_SECRET` | Your Spotify app client secret |
| `SPOTIPY_REFRESH_TOKEN` | The refresh token from step 3 |

**Variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|----------|-------|
| `SPOTIPY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` |
| `SPOTIFY_RADIO_PLAYLIST_ID` | Spotify playlist ID for the radio playlist |

The full Spotify playlist catalog is pushed to deejay-marvel-api when **`KAIANO_API_BASE_URL`** is set (see `docs/CONFIGURATION.md`); no separate snapshot path is required.

To find a playlist ID: open the playlist in Spotify, click the three-dot
menu → Share → Copy link. The ID is the string after `/playlist/` and
before any `?`.

---

## 5. Verify

Trigger the **Process New CSV Files** workflow manually via `workflow_dispatch`.
Check the logs for:

```
✅ Spotify API initialized
```

If you see credential errors, double-check that the secret names exactly
match those listed above.
