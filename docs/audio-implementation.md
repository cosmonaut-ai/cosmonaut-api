# Audio Narration (TTS) — Implementation Guide

## Overview

Story nodes can have AI-generated audio narrations via ElevenLabs TTS. Customers choose from a **pre-defined list of voices**. The backend generates MP3 audio on demand, stores it on S3/CDN (one file per node/voice combination), and returns a permanent URL. Audio generation is **usage-capped** per subscription tier, with a special lifetime-cap rule for free users.

---

## Backend Summary

- **Model**: ElevenLabs Flash 2.5 (`eleven_flash_v2_5`) — generates in ~1-3 seconds.
- **Voices**: 7 pre-defined voices, each with an internal `id`, a `display_name`, and a private ElevenLabs voice ID. Only `id` and `display_name` are exposed through the API.
- **Storage**: MP3 files stored in S3 at `audio/{world_id}/{node_id}/{voice_id}.mp3`, served via CloudFront CDN. URLs are permanent and publicly accessible.
- **Multi-voice per node**: Each node can have audio generated for multiple voices. Each voice/node combination is stored and served independently.
- **Idempotency**: If audio already exists for a given node + voice, the endpoint returns the cached URL instantly without consuming quota.
- **Race protection**: Concurrent requests for the same node + voice are safe — only the first generation consumes quota.

### Available Voices

| Internal ID  | Display Name |
| ------------ | ------------ |
| `riley`      | Riley        |
| `katherine`  | Katherine    |
| `paige`      | Paige        |
| `peter`      | Peter        |
| `theo`       | Theo         |
| `michael`    | Michael      |
| `jon`        | Jon          |

### Tier Limits

| Tier       | Audio narrations per period | Period  | Reset behaviour                    |
| ---------- | --------------------------- | ------- | ---------------------------------- |
| FREE       | 20                          | 7 days  | **Never resets** (lifetime cap)    |
| EXPLORER   | 60                          | 30 days | Resets each billing period         |
| COSMONAUT  | 200                         | 30 days | Resets each billing period         |

> Free-tier audio is a one-time allowance — once the 20 narrations are used, the user must upgrade to generate more. Paid tiers get fresh quota each billing cycle.

---

## API Reference

### List Available Voices

```
GET /voices/
```

**Auth**: None required (public endpoint).

**Success response** (`200 OK`):

```json
[
  {
    "id": "riley",
    "display_name": "Riley",
    "description": "A warm, midrange voice with a natural conversational tone.",
    "sample_url": "https://images.dev.cosmonaut-ai.com/voices/riley/sample.mp3"
  },
  {
    "id": "katherine",
    "display_name": "Katherine",
    "description": "A smooth, British-accented voice with refined elegance.",
    "sample_url": "https://images.dev.cosmonaut-ai.com/voices/katherine/sample.mp3"
  }
]
```

Each voice includes:
- `id` -- internal identifier used in API requests.
- `display_name` -- human-readable name for UI display.
- `description` -- short blurb describing the voice character.
- `sample_url` -- CDN URL to a sample MP3 so users can preview the voice before generating.

> **Note**: Sample MP3 files must be uploaded to S3 at `voices/{voice_id}/sample.mp3` for each voice.

### Generate Audio for a Node

```
POST /worlds/{world_id}/nodes/{node_id}/audio
```

**Auth**: Requires a valid JWT (same as all `/worlds` endpoints).

**Preconditions**:
- The node must exist.
- The node's `generation_status` must be `"completed"` (i.e., text has been fully generated). If text is still streaming or hasn't been generated, the endpoint returns `400`.

**Request body**:

```json
{
  "voice_id": "riley"
}
```

| Field      | Type   | Required | Description                                        |
| ---------- | ------ | -------- | -------------------------------------------------- |
| `voice_id` | string | Yes      | Internal voice ID from the `/voices/` endpoint.    |

**Success response** (`200 OK`):

```json
{
  "audio_url": "https://images.dev.cosmonaut-ai.com/audio/{world_id}/{node_id}/riley.mp3"
}
```

The `audio_url` is a permanent CDN link to the MP3 file. It can be used directly in an HTML `<audio>` element.

**Error responses**:

| Status | Condition                             | Response `detail`                                  |
| ------ | ------------------------------------- | -------------------------------------------------- |
| `400`  | Unknown voice_id                      | `"Unknown voice_id: {voice_id}"`                   |
| `400`  | Node text not yet generated           | `"Node {node_id} text has not been generated yet"` |
| `403`  | User not authorized for this world    | `"You are not authorized to access world ..."`     |
| `404`  | World or node not found               | `"World {id} not found"` / `"Node {id} not found"` |
| `429`  | Audio quota exceeded                  | `"Quota exceeded: audio limit is {limit}"`         |
| `500`  | ElevenLabs or S3 failure              | `"Audio generation failed"`                        |

> The `429` response is the key one for triggering the upgrade prompt on the frontend.

---

### Get Usage Info

```
GET /auth/usage
```

The existing usage endpoint includes audio fields:

```json
{
  "tier": "EXPLORER",
  "nodes_used": 42,
  "nodes_limit": 500,
  "worlds_created": 3,
  "worlds_limit": 20,
  "worlds_stored": 3,
  "worlds_stored_limit": 50,
  "audio_narrations_used": 12,
  "audio_narrations_limit": 60,
  "period_end": "2026-03-08T00:00:00+00:00",
  "pending_cancellation": false,
  "cancellation_date": null,
  "subscription_status": "active",
  "pending_tier": null,
  "pending_tier_date": null
}
```

- `audio_narrations_used` — how many audio narrations the user has generated this period (or lifetime for free tier).
- `audio_narrations_limit` — the user's tier cap.

---

### Story Node DTO

All endpoints that return a `StoryNodeDTO` now include an `audio` dictionary mapping voice IDs to CDN URLs:

```json
{
  "id": "0a",
  "world_id": "abc123",
  "text": "The ancient door creaked open...",
  "title": "The Threshold",
  "generation_status": "completed",
  "audio": {
    "riley": "https://images.dev.cosmonaut-ai.com/audio/abc123/0a/riley.mp3",
    "theo": "https://images.dev.cosmonaut-ai.com/audio/abc123/0a/theo.mp3"
  },
  "...": "..."
}
```

- `audio` — `object`. A dictionary of `{voice_id: audio_url}`. Empty object `{}` if no audio has been generated for this node.

---

## Frontend Implementation Guide

### 1. API Client

Add functions to call the new endpoints:

```typescript
// Fetch available voices
async function listVoices(): Promise<{ id: string; display_name: string }[]> {
  const response = await fetch(`${API_BASE}/voices/`);
  return response.json();
}

// Generate audio with a specific voice
async function generateNodeAudio(
  worldId: string,
  nodeId: string,
  voiceId: string
): Promise<{ audio_url: string }> {
  const response = await fetch(`${API_BASE}/worlds/${worldId}/nodes/${nodeId}/audio`, {
    method: 'POST',
    headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice_id: voiceId }),
  });

  if (!response.ok) {
    if (response.status === 429) {
      throw new QuotaExceededError('audio');
    }
    const body = await response.json();
    throw new ApiError(response.status, body.detail);
  }

  return response.json();
}
```

### 2. Voice Selection UI

- Fetch available voices from `GET /voices/` (can be cached — the list is static).
- Present a voice picker (e.g., dropdown, radio buttons, or voice cards) before or alongside the audio play button.
- Store the user's selected voice preference in local storage for convenience.

### 3. Node View Integration

In `StoryNodeView.svelte` (or equivalent component):

**State**:
- `selectedVoiceId: string` — the user's chosen voice.
- `isGeneratingAudio: boolean` — true while the POST request is in flight.
- Use the node's `audio` dict from the DTO to determine if audio already exists for the selected voice.

**UI**:
- Add a narration button (e.g., speaker/Volume2 icon) near the existing controls.
- **Disable** the button when:
  - Text is still streaming (`isStreaming` or `isNodeGenerating`)
  - `generation_status !== "completed"`
  - `isGeneratingAudio` is true (show a spinner/loading state instead)

**Behaviour**:

```
if node.audio[selectedVoiceId] exists:
    → Play/pause toggle using <audio> element
else:
    → Call generateNodeAudio(worldId, nodeId, selectedVoiceId)
    → On success: update node.audio[selectedVoiceId] in local state, begin playback
    → On 429 error: show UpgradePrompt (reuse existing quota-exceeded pattern)
    → On other error: show toast/error message
```

**Playback**:
- Use a standard HTML `<audio>` element or a Svelte audio binding.
- The CDN URL can be set as the `src` directly — no auth headers needed for playback.
- Consider preloading: `<audio preload="none">` to avoid unnecessary bandwidth on page load.

### 4. Usage Display

Display audio usage alongside existing usage meters:

```
Audio narrations: 12 / 60
```

### 5. Clean Up

The existing browser-native `window.speechSynthesis` implementation should be removed or hidden behind a fallback option, since ElevenLabs audio is now the primary narration method.

---

## Sequence Diagram

```
User clicks "Play Audio" on a completed story node (with voice selected)
  │
  ├─ node.audio[selectedVoiceId] exists?
  │   ├─ YES → Play the MP3 directly (no API call)
  │   └─ NO  → POST /worlds/{worldId}/nodes/{nodeId}/audio
  │              Body: { "voice_id": "riley" }
  │              │
  │              ├─ 200 → { audio_url: "https://cdn.../audio/.../riley.mp3" }
  │              │         Update local node state, begin playback
  │              │
  │              ├─ 400 → Unknown voice or node text not ready
  │              │
  │              ├─ 429 → Quota exceeded → Show UpgradePrompt
  │              │
  │              └─ 500 → Generation failed → Show error toast
```

---

## Notes

- Audio generation takes ~1-3 seconds. The UI should show a loading/spinner state during this time.
- The returned `audio_url` is permanent — once generated, it never expires or changes. The frontend can cache it freely.
- The endpoint is idempotent per voice: calling it multiple times for the same node + voice always returns the same URL and only consumes quota once.
- Audio files are MP3 format, typically 100-300KB for a story node.
- Each voice generation counts as one audio narration toward the user's quota, even on the same node.
