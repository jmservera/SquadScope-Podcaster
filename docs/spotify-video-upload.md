# Spotify Video Upload — Complete Flow & Working Example

> **Status:** Proven working (June 2026). Documented from reverse-engineering the
> Spotify for Creators web app and validated with real uploads.

## Overview

Video podcast episodes on Spotify use a **multipart chunked upload to Google Cloud
Storage (GCS)** — different from the simpler single-PUT audio upload to S3. The
flow has 4 steps:

1. Request per-part signed URLs from Spotify's API
2. Upload each chunk (PUT) to its GCS signed URL
3. Notify Spotify that all parts are uploaded (`process_upload`)
4. Poll until server-side processing completes

## Prerequisites

- A valid `sp_dc` cookie (from `https://creators.spotify.com`)
- The show's `webId` (a base62 ID like `033xdn5nDMoCWxB3bss2dB`)
- A video file (MP4, H.264 + AAC, ≤ ~200MB practical, `faststart` recommended)

## Critical Constraints

### Audio duration MUST be ≤ video duration

Spotify's server-side validation rejects videos where the audio stream is longer
than the video stream with error:

```
AUDIO_DURATION_LONGER_THAN_VIDEO (FAILURE_TYPE_MAL_REJECTION)
```

**Fix before upload:**

```bash
# Get video stream duration
VIDEO_DUR=$(ffprobe -v error -select_streams v:0 \
  -show_entries stream=duration -of csv=p=0 input.mp4)

# Re-encode audio trimmed to video duration
ffmpeg -y -i input.mp4 \
  -c:v copy \
  -c:a aac -ac 2 -b:a 128k \
  -t "$VIDEO_DUR" \
  -movflags +faststart \
  output.mp4
```

### Video MUST go to GCS (not S3)

Without `uploadType=video` in the signedUrl request, the server routes the file
to S3 storage. Even if the upload succeeds, `process_upload` will reject it with:

```
"File is using invalid storage"
```

### Multipart format is required

Even for files smaller than one chunk, the server expects the multipart flow
(`isMultipartUpload=true&numParts=1`). A direct single-PUT to the signed URL
will result in `process_upload` returning HTTP 500.

## Step-by-Step Flow

### Step 1: Resolve legacy IDs

```http
GET https://api-v5.anchor.fm/v3/profile/station/webStationId/{WEB_ID}
Cookie: sp_dc={SP_DC}; sp_key={SP_KEY}
```

Response (extract `stationId` and `userId`):
```json
{
  "station": { "stationId": "46150077", "userId": "46104253", ... }
}
```

### Step 2: Create a draft episode

```http
POST https://api-v5.anchor.fm/v3/episodes
Cookie: sp_dc=...; sp_key=...
Content-Type: application/json
Origin: https://creators.spotify.com
Referer: https://creators.spotify.com/

{"stationId": "46150077", "title": "Untitled"}
```

Response:
```json
{"episodeId": 1234567890}
```

The `episodeId` is called `anchor_id` in subsequent requests.

### Step 3: Request multipart signed URLs

```http
GET https://api-v5.anchor.fm/v3/episodes/{ANCHOR_ID}/upload/signedUrl
  ?filename=episode.mp4
  &type=video/mp4
  &isMumsCompatible=true
  &isMultipartUpload=true
  &numParts=2
  &uploadType=video
Cookie: sp_dc=...; sp_key=...
```

**Parameters:**
| Param | Required | Description |
|-------|----------|-------------|
| `filename` | Yes | Original filename |
| `type` | Yes | MIME type (`video/mp4`) |
| `isMumsCompatible` | Yes | Always `true` |
| `isMultipartUpload` | Yes | Must be `true` for video |
| `numParts` | Yes | Number of chunks (ceil(filesize / chunk_size)) |
| `uploadType` | Yes | Must be `video` — routes to GCS |

**Response:**
```json
{
  "requestUuid": "a1b2c3d4-e5f6-...",
  "signedUrlParts": [
    {"partNumber": 1, "url": "https://storage.googleapis.com/anchor-audio-upload/...?X-Goog-Signature=..."},
    {"partNumber": 2, "url": "https://storage.googleapis.com/anchor-audio-upload/...?X-Goog-Signature=..."}
  ],
  "fileKey": "anchor-audio-upload/...",
  "signedUrl": "https://storage.googleapis.com/..."
}
```

> **Note:** The response uses S3-era field names (`requestUuid`, `signedUrl`) even
> though storage is GCS. Use `signedUrlParts` for the actual upload — ignore
> `signedUrl` (it's the base URL without part suffixes).

### Step 4: Upload each chunk

Split the file into chunks and PUT each one to its corresponding `signedUrlParts[i].url`:

```http
PUT {signedUrlParts[0].url}
Referer: https://creators.spotify.com/
Content-Length: 31457280

<binary chunk data>
```

**Headers:**
- `Referer: https://creators.spotify.com/` — required
- Do **NOT** send `Content-Type`, `Origin`, or `Authorization`
- The GCS signed URL has `X-Goog-SignedHeaders=host` (only host is verified)

**Response:** HTTP 200 with `ETag` header — save this for each part.

**Chunk size:** ~30MB recommended (browser uses this). Minimum 5MB except for the
last chunk.

### Step 5: Notify upload complete (`process_upload`)

```http
POST https://api-v5.anchor.fm/v3/upload/{REQUEST_UUID}/process_upload
  ?isMumsCompatible=true
Cookie: sp_dc=...; sp_key=...
Content-Type: application/json
Origin: https://creators.spotify.com
Referer: https://creators.spotify.com/

{
  "userId": 46104253,
  "uploadType": "video",
  "origin": "episode-media:upload",
  "caption": "episode.mp4",
  "isExtractedFromVideo": true,
  "isMultipartUpload": true,
  "parts": [
    {"partNumber": 1, "etag": "abc123..."},
    {"partNumber": 2, "etag": "def456..."}
  ],
  "uploadId": "a1b2c3d4-e5f6-...",
  "episodeId": 1234567890,
  "stationId": 46150077
}
```

**Response:** HTTP 200 (no meaningful body — processing is async).

### Step 6: Poll for processing completion

```http
GET https://api-v5.anchor.fm/v3/upload/media/{REQUEST_UUID}
  ?includeMediaValidation=true
  &isMumsCompatible=true
Cookie: sp_dc=...; sp_key=...
```

**Response (processing):**
```json
{
  "request": {
    "state": "uploaded",
    "requestUuid": "a1b2c3d4-..."
  }
}
```

**Response (success):**
```json
{
  "request": {
    "state": "processed",
    "requestUuid": "a1b2c3d4-..."
  },
  "mediaValidation": {
    "status": "validation_success"
  }
}
```

**Response (failure):**
```json
{
  "request": {
    "state": "failed",
    "failureReason": ""
  },
  "mediaValidation": {
    "status": "validation_failure",
    "failures": [
      {"reason": "AUDIO_DURATION_LONGER_THAN_VIDEO", "type": "FAILURE_TYPE_MAL_REJECTION"}
    ]
  }
}
```

**Terminal states:** `processed` = success, `failed` = failure.

> **Known quirk:** The poll endpoint sometimes returns HTTP 404. This is transient
> — retry with exponential backoff (up to 300s total wait). The media record
> appears within a few seconds.

## Complete Working Python Example

```python
"""
Spotify Video Podcast Upload — Complete Working Example

Requires: requests, python-dotenv (optional)
Environment: SP_DC, SP_KEY, SPOTIFY_SHOW_ID
"""

import math
import os
import time
from pathlib import Path

import requests

BASE_URL = "https://api-v5.anchor.fm"
CHUNK_SIZE = 30 * 1024 * 1024  # 30MB per chunk


def upload_video_episode(
    video_path: str,
    title: str = "Untitled",
    sp_dc: str | None = None,
    sp_key: str | None = None,
    show_id: str | None = None,
) -> dict:
    """Upload a video file as a Spotify podcast episode draft.

    Returns dict with episode info on success, raises on failure.
    """
    sp_dc = sp_dc or os.environ["SP_DC"]
    sp_key = sp_key or os.environ["SP_KEY"]
    show_id = show_id or os.environ["SPOTIFY_SHOW_ID"]
    video_file = Path(video_path)
    video_data = video_file.read_bytes()
    file_size = len(video_data)
    num_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    # Build session
    session = requests.Session()
    session.cookies.set("sp_dc", sp_dc)
    session.cookies.set("sp_key", sp_key)
    session.headers.update({
        "Origin": "https://creators.spotify.com",
        "Referer": "https://creators.spotify.com/",
    })

    # 1. Resolve station/user IDs
    resp = session.get(f"{BASE_URL}/v3/profile/station/webStationId/{show_id}")
    resp.raise_for_status()
    station = resp.json()["station"]
    station_id = int(station["stationId"])
    user_id = int(station["userId"])
    print(f"Station: {station_id}, User: {user_id}")

    # 2. Create draft episode
    resp = session.post(
        f"{BASE_URL}/v3/episodes",
        json={"stationId": str(station_id), "title": title},
    )
    resp.raise_for_status()
    anchor_id = resp.json()["episodeId"]
    print(f"Created draft episode: {anchor_id}")

    # 3. Get multipart signed URLs
    resp = session.get(
        f"{BASE_URL}/v3/episodes/{anchor_id}/upload/signedUrl",
        params={
            "filename": video_file.name,
            "type": "video/mp4",
            "isMumsCompatible": "true",
            "isMultipartUpload": "true",
            "numParts": str(num_parts),
            "uploadType": "video",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    request_uuid = data["requestUuid"]
    signed_parts = data["signedUrlParts"]
    print(f"Got {len(signed_parts)} signed URLs, requestUuid={request_uuid}")

    # 4. Upload each chunk
    parts_etags = []
    for i, part in enumerate(signed_parts):
        start = i * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, file_size)
        chunk = video_data[start:end]

        resp = requests.put(
            part["url"],
            data=chunk,
            headers={"Referer": "https://creators.spotify.com/"},
            timeout=300,
        )
        resp.raise_for_status()
        etag = resp.headers["ETag"].strip('"')
        parts_etags.append({"partNumber": part["partNumber"], "etag": etag})
        print(f"  Part {part['partNumber']}/{len(signed_parts)}: "
              f"{len(chunk)} bytes, ETag={etag[:12]}...")

    # 5. Process upload (notify all parts uploaded)
    resp = session.post(
        f"{BASE_URL}/v3/upload/{request_uuid}/process_upload",
        params={"isMumsCompatible": "true"},
        json={
            "userId": user_id,
            "uploadType": "video",
            "origin": "episode-media:upload",
            "caption": video_file.name,
            "isExtractedFromVideo": True,
            "isMultipartUpload": True,
            "parts": parts_etags,
            "uploadId": request_uuid,
            "episodeId": anchor_id,
            "stationId": station_id,
        },
    )
    resp.raise_for_status()
    print("process_upload accepted, polling...")

    # 6. Poll for completion (with backoff for 404s)
    poll_url = f"{BASE_URL}/v3/upload/media/{request_uuid}"
    backoff = 3
    for attempt in range(60):  # up to ~300s
        time.sleep(backoff)
        resp = session.get(
            poll_url,
            params={
                "includeMediaValidation": "true",
                "isMumsCompatible": "true",
            },
        )
        if resp.status_code == 404:
            backoff = min(backoff * 1.5, 30)
            print(f"  Poll 404 (transient), backing off to {backoff:.0f}s...")
            continue

        resp.raise_for_status()
        result = resp.json()
        req_data = result.get("request", result)
        state = req_data.get("state", "")

        if state == "processed":
            validation = result.get("mediaValidation", {})
            print(f"✅ Upload processed! Validation: {validation.get('status')}")
            return {
                "anchor_id": anchor_id,
                "request_uuid": request_uuid,
                "state": state,
                "validation": validation.get("status"),
            }
        elif state == "failed":
            validation = result.get("mediaValidation", {})
            failures = validation.get("failures", [])
            raise RuntimeError(
                f"Upload failed: {req_data.get('failureReason')} "
                f"Validation: {[f.get('reason') for f in failures]}"
            )
        else:
            backoff = 3  # reset on non-404
            print(f"  State: {state} (attempt {attempt + 1})")

    raise TimeoutError("Upload processing timed out after 300s")


if __name__ == "__main__":
    import sys

    video = sys.argv[1] if len(sys.argv) > 1 else "podcast_video.mp4"
    result = upload_video_episode(video, title="Test Video Episode")
    print(f"\nResult: {result}")
```

## Known Quirks

| Quirk | Workaround |
|-------|------------|
| Poll returns 404 | Transient; retry with exponential backoff up to 300s |
| `failureReason` is empty string on validation failures | Check `mediaValidation.failures[]` for the real reason |
| `signedUrl` in response is not usable for multipart | Use `signedUrlParts[].url` instead |
| Response field names are S3-era (`requestUuid` not `uploadId`) | Handle both: `data.get("uploadId") or data["requestUuid"]` |
| Audio longer than video by even 0.01s → rejection | Always trim audio to exact video duration before upload |
| Files must use GCS for video | Always pass `uploadType=video` in signedUrl request |
| `state=processed` (not `completed`) is success for video | Check both states for compatibility |

## Audio vs Video Upload Comparison

| Aspect | Audio (MP3) | Video (MP4) |
|--------|-------------|-------------|
| Storage | S3 | GCS |
| Upload method | Single PUT | Multipart chunked |
| `uploadType` param | (omitted) | `video` |
| `isMultipartUpload` | `false` or omitted | `true` |
| Signed URL response | `signedUrl` (single URL) | `signedUrlParts` (array) |
| PUT headers | Content-Type + Origin + Referer | Referer only |
| Success terminal state | `completed` | `processed` |
| `isExtractedFromVideo` | `false` | `true` |
| Max chunk size | N/A (single file) | ~30MB recommended (5MB minimum) |

## Video File Recommendations

```bash
# Encode a video podcast-ready MP4:
ffmpeg -y -i raw_video.mp4 \
  -c:v libx264 -preset medium -crf 23 \
  -vf "scale=1280:720" \
  -c:a aac -ac 2 -b:a 128k \
  -t $(ffprobe -v error -select_streams v:0 \
       -show_entries stream=duration -of csv=p=0 raw_video.mp4) \
  -movflags +faststart \
  output.mp4
```

**Key settings:**
- **Resolution:** 720p or 1080p (720p keeps file sizes manageable)
- **Codec:** H.264 High profile + AAC stereo
- **`-movflags +faststart`:** Moves moov atom to start for streaming
- **`-t {video_duration}`:** Trims audio to match video exactly
- **Stereo audio required** (mono may work but stereo is what the web app sends)
