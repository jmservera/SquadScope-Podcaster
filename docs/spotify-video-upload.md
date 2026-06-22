# Spotify Video Pipeline — Working Specs & Rollback Reference

> **Status:** Proven working (June 2026). This document is the **rollback
> reference** for the complete video podcast pipeline — from screen recording
> through Spotify upload. If a future change breaks video, revert to the
> known-good state below and consult these specs.
>
> **Known-good commit:** [`5a7abbc`](https://github.com/jmservera/SquadScope-Podcaster/commit/5a7abbc)
> — *"Fix #355: Video composition sync to audio, fit-to-window trim, and
> recording quality (#356)"*. If video regresses, this is the commit to roll
> back to (or diff against). The Spotify upload API behaviour was last validated
> at `c3b4ba4`/`5a7abbc`.

All code paths cited below live in `podcaster/video/` (composition, recording,
distribution) and `podcaster/publish.py` (Spotify upload API). Constants are in
`podcaster/video/video_compose.py`.

---

## 1. Pipeline Flow

The end-to-end flow that turns recorded repo footage into a Spotify-ready MP4:

```
record → normalize → fit-to-window → pairwise compose → join (intro/content/outro)
       → audio overlay → h264_metadata BSF → upload
```

| Stage | Where | What happens |
|-------|-------|--------------|
| **record** | `video_gen.record_episode` | Playwright records each repo page (and generic segments) as a 1920×1080 WebM via `record_video_size`/`viewport` = `WIDTH×HEIGHT` (1920×1080). |
| **(sync)** | `video_compose.build_sync_map` / `apply_sync` | Optional: matches recordings to the timed episode plan and stream-copy trims overlong recordings to their target window (`-c copy`, no re-encode). |
| **normalize / fit** | `_build_normalize_cmd` / `_build_fit_segment_cmd` | Each segment is scaled+padded to 1920×1080@30fps, bt709, yuv420p. When `audio_duration` is known, segments are **fit** to exact target durations (trim or freeze-extend) — see §4. |
| **pairwise compose** | `_compose_pairwise` / `_build_xfade_step_cmd` | Segments are crossfaded **two at a time** (`xfade`), accumulating into one video. Pairwise (not N-input `filter_complex`) keeps memory constant — the old N-input filter OOMed at ~18 segments. Lower-thirds and the DOG watermark are baked in here. |
| **join bookends** | `_join_intro_outro` | Intro and outro clips (fetched from blob storage, cached) are canonicalized to a uniform video-only AV layout and concat-demuxed as `intro → content → outro`. Their source audio is always stripped. |
| **audio overlay** | `_build_audio_overlay_cmd` | The podcast MP3 is re-encoded to AAC and mapped as the **sole** audio track across the whole joined video. Never `-shortest` (outro audio must play in full). Video/audio duration are reconciled here — see §6. |
| **h264_metadata BSF** | `_build_h264_metadata_cmd` | Final stream-copy pass rewriting H.264 VUI colour metadata to one consistent BT.709 set. Always runs — see §3. |
| **upload** | `publish.upload_video_to_episode` via `distribution.upload_to_spotify_episode` | Multipart chunked upload to Spotify/GCS as a **new** draft episode — see §5. |

The orchestration is `compose_video()`; the output is always `output_path`,
written only by the final BSF pass (which requires a distinct input/output file,
so the composed content is never written directly to `output_path`).

### Canonical output format (constants)

| Constant | Value | Notes |
|----------|-------|-------|
| `OUTPUT_WIDTH × OUTPUT_HEIGHT` | `1920 × 1080` | 1080p |
| `OUTPUT_FPS` | `30` | |
| `ENCODE_PRESET` | `slow` | final encodes; intermediates use `ultrafast` |
| `ENCODE_CRF` | `18` | |
| `ENCODE_PIX_FMT` | `yuv420p` | |
| `ENCODE_AUDIO_BITRATE` | `192k` | |
| `CONCAT_AUDIO_SAMPLE_RATE / CHANNELS` | `48000` / `2` | stereo AAC |
| `TRANSITION_DURATION` | `1.0 s` | one xfade per boundary |
| `MIN_CONTENT_WINDOW_SECONDS` | `1.0` | fit-to-window floor |
| `OUTRO_VIDEO_FADE_SECONDS` | `2.0` | fade-to-black when video is freeze-extended |
| `INTRO_BLOB_PATH / OUTRO_BLOB_PATH` | `assets/video/intro.mp4` / `assets/video/outro.mp4` | |

Every encode pass (`_build_normalize_cmd`, `_build_fit_segment_cmd`,
`_build_canonical_av_cmd`, `_encode_tail`/`_BT709_FLAGS`,
`_build_audio_overlay_cmd`) sets the same colour flags:
`-colorspace bt709 -color_trc bt709 -color_primaries bt709 -color_range tv`.

---

## 2. Spotify Requirements

These are the server-side constraints Spotify's validation enforces. Each is
prevented by a specific stage above.

| Requirement | Why | Enforced by |
|-------------|-----|-------------|
| **BT.709 colour, consistent across all NAL units** | Mixed/inconsistent SPS VUI colour → `INCONSISTENT_COLOR_DETAILS`. | Every encode sets bt709 flags **and** the final `h264_metadata` BSF normalises VUI (§3). |
| **Audio duration ≥ video duration** | If video outlasts audio → `VIDEO_DURATION_LONGER_THAN_AUDIO`. | `_build_audio_overlay_cmd` pads audio with `apad=whole_dur` to the video length (§6). |
| **Audio not longer than video** (legacy audio-on-video flow) | Older constraint: `AUDIO_DURATION_LONGER_THAN_VIDEO`. | When audio outlasts video, the final frame is freeze-extended (`tpad=stop_mode=clone`) + fade-to-black, so video ≥ audio. |
| **Keyframes / regular GOP** | Spotify needs seekable keyframes; clips are re-encoded with libx264 defaults (regular GOP/IDR cadence at 30fps). | All re-encodes use `libx264` (not stream-copy of arbitrary GOPs); the BSF pass is copy-only and preserves the encoder's keyframes. |
| **PTS offset / monotonic timestamps** | Crossfades and concat must not produce negative/overlapping PTS. | `xfade` uses an explicit `offset = cumulative − transition_duration` per pass; `tpad` extends PTS forward; `-movflags +faststart` moves the moov atom to the front. |
| **MP4 H.264 + stereo AAC, faststart** | Required container/codec. | Canonical encode (yuv420p H.264 + 48 kHz stereo AAC) with `-movflags +faststart`. |
| **File size / chunking** | Large files must be multipart. | 30 MB chunks; `numParts = ceil(filesize / 30 MB)` (§5). |

> **PTS offset detail:** in `_compose_pairwise`, each xfade pass sets
> `offset = cumulative - transition_duration`, where `cumulative` accumulates
> `durations[i] - transition_duration`. This places each crossfade exactly one
> transition-length before the running end of the accumulator, keeping the
> composed timeline's presentation timestamps continuous.

---

## 3. h264_metadata Bitstream Filter

**Command** (`_build_h264_metadata_cmd`, stream-copy — no re-encode):

```bash
ffmpeg -hide_banner -loglevel warning -y \
  -i pre_final.mp4 \
  -c:v copy \
  -bsf:v h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0 \
  -c:a copy \
  -movflags +faststart \
  output.mp4
```

**Why it is required (issue #353):** The concat demuxer copies H.264 NAL units
from independently-encoded clips (intro, content, outro) whose SPS VUI colour
data can disagree — even when every encode passed identical bt709 flags, the
muxed stream can carry inconsistent `colour_primaries` /
`transfer_characteristics` / `matrix_coefficients`. Spotify's validator then
fails with `INCONSISTENT_COLOR_DETAILS`.

The `h264_metadata` BSF rewrites those three VUI fields to BT.709 (value `1`)
and sets `video_full_range_flag=0` (limited/`tv` range) **in the bitstream
itself**, without re-encoding. This guarantees a single consistent colour
description across the whole file. The pass **always runs** as the last step of
`compose_video`, which is why composition never writes directly to
`output_path` (the BSF needs a distinct input and output file).

---

## 4. Fit-to-Window Logic

When `compose_video` receives a positive `audio_duration`, content segments are
**fit to the audio timeline** (issue #355) so the right repo is on screen while
the hosts discuss it, and the intro/outro bumpers always play in full.

### Formulas

```
content_window = max(audio_duration − intro_duration − outro_duration,
                     MIN_CONTENT_WINDOW_SECONDS)         # 1.0 s floor

# Because adjacent segments overlap by one transition per boundary, the
# per-segment durations must sum to slightly MORE than the window:
overlap_total = transition_duration × (n − 1)
target_sum    = content_window + overlap_total

# Proportional scaling preserves each segment's share of the timeline:
scale     = target_sum / sum(plan_durations)
scaled[i] = plan_durations[i] × scale

# Each xfade boundary needs both clips strictly longer than the transition:
floor     = transition_duration + 0.5
floored[i]= max(scaled[i], floor)

# Flooring can overshoot target_sum; redistribute the excess across the
# headroom each segment has above `floor` so the sum returns to target_sum:
headroom[i] = floored[i] − floor
floored[i] -= excess × headroom[i] / sum(headroom)      # when feasible
```

(`_fit_target_durations` in `video_compose.py`.)

### Per-segment fitting (`_build_fit_segment_cmd`)

Each segment is forced to its exact `target_duration` in a single pass:

* **Source longer than target** → trimmed by `-t target`.
* **Source shorter than target** → its final frame is held
  (`tpad=stop_mode=clone:stop_duration={target}`) and then cut to `-t target`.

`tpad` appends up to `target` extra seconds of cloned frames *after* the source
ends, so `-t target` always has enough material regardless of how short the
recording is — one pass that both trims and freeze-extends without probing the
source length.

The composed content video duration is
`sum(durations) − transition_duration × (n − 1)`, i.e. exactly
`content_window`; `_join_intro_outro` then adds the real intro/outro lengths
back so the final video matches the audio.

---

## 5. Upload Flow

> **Status:** Proven working (June 2026). Documented from reverse-engineering the
> Spotify for Creators web app and validated with real uploads. In the engine
> this is `podcaster.publish.upload_video_to_episode`, invoked by
> `distribution.upload_to_spotify_episode` when `VIDEO_SPOTIFY_UPLOAD_ENABLED`.

Video podcast episodes on Spotify use a **multipart chunked upload to Google Cloud
Storage (GCS)** — different from the simpler single-PUT audio upload to S3. The
video is published as a **new, separate draft episode** (issue #340): Spotify
rejects attaching a video to an episode that already holds audio, so the audio
episode (`anchor_id`) is referenced only for logging and never modified.

The flow has these steps:

1. Create a new draft episode (never reuse the audio episode)
2. Request per-part signed URLs (`uploadType=video`, `isMultipartUpload=true`,
   `numParts = ceil(filesize / 30 MB)`)
3. Upload each **30 MB chunk** (PUT) to its GCS signed URL, collecting ETags
4. Notify Spotify that all parts are uploaded (`process_upload`)
5. Poll until server-side processing completes (`state=processed`)
6. Set episode metadata (title, description)

Chunk size is `_VIDEO_CHUNK_SIZE = 30 * 1024 * 1024` (30 MB) in
`podcaster/publish.py`.

### Upload API reference (detailed)

> The subsections below are the low-level Spotify/Anchor API reference (the
> exact HTTP calls, headers, and a standalone working example) used by
> `podcaster/publish.py`. They are preserved verbatim as the authoritative
> protocol documentation.

#### Prerequisites

- A valid `sp_dc` cookie (from `https://creators.spotify.com`)
- The show's `webId` (a base62 ID like `033xdn5nDMoCWxB3bss2dB`)
- A video file (MP4, H.264 + AAC, ≤ ~200MB practical, `faststart` recommended)

#### Critical Constraints

##### Audio duration MUST be ≤ video duration

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

##### Video MUST go to GCS (not S3)

Without `uploadType=video` in the signedUrl request, the server routes the file
to S3 storage. Even if the upload succeeds, `process_upload` will reject it with:

```
"File is using invalid storage"
```

##### Multipart format is required

Even for files smaller than one chunk, the server expects the multipart flow
(`isMultipartUpload=true&numParts=1`). A direct single-PUT to the signed URL
will result in `process_upload` returning HTTP 500.

#### Step-by-Step Flow

##### Step 1: Resolve legacy IDs

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

##### Step 2: Create a draft episode

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

##### Step 3: Request multipart signed URLs

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

##### Step 4: Upload each chunk

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

##### Step 5: Notify upload complete (`process_upload`)

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

##### Step 6: Poll for processing completion

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

#### Complete Working Python Example

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

#### Known Quirks

| Quirk | Workaround |
|-------|------------|
| Poll returns 404 | Transient; retry with exponential backoff up to 300s |
| `failureReason` is empty string on validation failures | Check `mediaValidation.failures[]` for the real reason |
| `signedUrl` in response is not usable for multipart | Use `signedUrlParts[].url` instead |
| Response field names are S3-era (`requestUuid` not `uploadId`) | Handle both: `data.get("uploadId") or data["requestUuid"]` |
| Audio longer than video by even 0.01s → rejection | Always trim audio to exact video duration before upload |
| Files must use GCS for video | Always pass `uploadType=video` in signedUrl request |
| `state=processed` (not `completed`) is success for video | Check both states for compatibility |

#### Audio vs Video Upload Comparison

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

#### Video File Recommendations

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

---

## 6. Error Codes & Prevention

Spotify surfaces validation failures during the poll step (§5, Step 6). The
engine extracts the human reason from `mediaValidation.failures[].reason` **and**
the precise machine code from `mediaValidation.failureInfo.errorCode` (issue
#351 / `podcaster/publish.py` `_poll_upload_status`).

| Error code / reason | Cause | Prevented by |
|---------------------|-------|--------------|
| `INCONSISTENT_COLOR_DETAILS` | The concatenated H.264 stream carries disagreeing SPS VUI colour metadata across intro/content/outro NAL units. | (a) every encode pass sets `-colorspace/-color_trc/-color_primaries bt709 -color_range tv`; (b) the final `h264_metadata` BSF rewrites VUI to a single BT.709/limited-range set (§3). |
| `VIDEO_DURATION_LONGER_THAN_AUDIO` | The video stream outlasts the audio stream. | `_build_audio_overlay_cmd` pads the audio with `-af apad=whole_dur={video_duration}` when `0 < audio_duration < video_duration`, so audio ≥ video. |
| `AUDIO_DURATION_LONGER_THAN_VIDEO` (legacy) | The audio stream outlasts the video stream. | When `audio_duration > video_duration`, the final frame is held (`tpad=stop_mode=clone:stop_duration={pad}`) + faded to black (`fade=t=out`, `OUTRO_VIDEO_FADE_SECONDS`), extending video to ≥ audio. The outro audio is **never** truncated (no `-shortest`). |
| `"File is using invalid storage"` | The signed-URL request omitted `uploadType=video`, routing the file to S3 instead of GCS. | Always pass `uploadType=video` (and `isMultipartUpload=true`). |
| `process_upload` HTTP 500 | A single-PUT upload was used for video. | Always use the multipart flow (`numParts = ceil(filesize / 30 MB)`), even for one chunk. |

> **Audio/video duration reconciliation (the heart of error prevention).**
> `_build_audio_overlay_cmd` probes both durations and:
> - **video shorter than audio** → freeze-extend the last video frame and fade to
>   black (video stream re-encoded with bt709 flags);
> - **video longer than audio** → `apad=whole_dur` the audio with trailing silence;
> - **equal** → stream-copy the video, leave audio as-is.
>
> The output is never cut with `-shortest`, guaranteeing the outro plays fully
> while still satisfying both duration constraints above.

When a failure does occur, `SpotifyPublishError` includes the `errorCode` and
full `failureInfo` so logs pinpoint the exact rejection.

---

## 7. Known-Good Commit

See the banner at the top of this document: **`5a7abbc`**
("Fix #355: Video composition sync to audio, fit-to-window trim, and recording
quality (#356)"). Roll back to or diff against this commit if video regresses.
The Spotify multipart upload protocol (§5) was validated against real uploads at
`c3b4ba4` and remains unchanged through `5a7abbc`.

---

## 8. Environment Variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `VIDEO_SPOTIFY_UPLOAD_ENABLED` | `distribution.VideoDistributionConfig.from_env` | `"true"` enables publishing the MP4 as a new Spotify video episode draft (§5). |
| `SP_DC` | `publish._get_credentials` | Spotify `sp_dc` session cookie (auth). |
| `SP_KEY` | `publish._build_session` | Spotify `sp_key` session cookie (auth). |
| `SPOTIFY_SHOW_ID` | `publish._get_credentials` | The show's `webId` used to resolve legacy `stationId`/`userId`. |
| `PODCASTER_STORAGE_ACCOUNT_URL` | `storage.py`, `video/job_runner.py` | Azure Blob storage account URL; backs intro/outro fetch, blob archive, and job manifests. |

Adjacent distribution toggles (same `from_env`): `VIDEO_YOUTUBE_ENABLED`,
`VIDEO_SPOTIFY_RSS_ENABLED`, `VIDEO_BLOB_ARCHIVE_ENABLED` (defaults `true`),
`VIDEO_DISTRIBUTE_DRY_RUN`. At least one distribution target must be enabled or
`distribute_video` aborts. `PODCASTER_VIDEO_QUEUE` selects the job queue
(default `video-jobs`).

Credentials are never hardcoded; they come from the environment (managed
identity / Key Vault in Azure). Do not commit `SP_DC`/`SP_KEY`.

---

## 9. Testing Strategy

A three-tier escalation, cheapest and fastest first:

1. **Local (unit tests)** — `pytest tests/ -q` (297+ tests). The video suites
   (`tests/test_video_compose.py`, `test_video_distribution.py`,
   `test_video_gen.py`, `test_video_intro_outro.py`, `test_video_job_runner.py`,
   `test_video_sync_plan.py`, `test_video_zoom.py`) assert on the **exact ffmpeg
   command strings** via a `FakeCommandRunner`, so the bt709 flags, the
   `h264_metadata` BSF arguments, the `tpad`/`apad` duration logic, and the
   `_fit_target_durations` maths are all verified without invoking ffmpeg. Run
   these on every change.
2. **Local (integration)** — `tests/integration/test_video_pipeline.py`
   (`pytest -m integration`) exercises `compose_video` end-to-end with a fake
   runner, asserting full pipeline ordering (normalize → compose → join → audio
   → BSF). For a real ffmpeg/render check, run the pipeline against small sample
   clips on a workstation that has system ffmpeg with libfreetype (for
   drawtext/lower-thirds).
3. **ACA (Azure Container Apps)** — deploy the synthesis/video job container and
   run a real episode so the actual ffmpeg binary, Playwright recording, blob
   intro/outro fetch, and memory behaviour (pairwise compose avoids the
   ~18-segment OOM) are validated in the production environment.
4. **GitHub Action** — `.github/workflows/integration-tests.yml` runs the
   integration suite in CI; deploy/publish workflows (`deploy-azure.yml`,
   `synthesis-image-publish.yml`) ship the validated container. A real Spotify
   upload is the final gate, validated manually against a draft episode.

> **CI must be correct, not just green:** never weaken the ffmpeg-command
> assertions or skip the colour/duration checks to make a build pass — those
> assertions are exactly what protect against the §6 Spotify rejections.
