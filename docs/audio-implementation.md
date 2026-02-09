# Audio Narration (TTS) — Implementation Guide

## Overview

Story nodes can now have AI-generated audio narrations via ElevenLabs TTS. The backend generates MP3 audio on demand, stores it on S3/CDN, and returns a permanent URL. Audio generation is **usage-capped** per subscription tier, with a special lifetime-cap rule for free users.

---

## Backend Summary (Already Implemented)

The following is already live on the API. No further backend work is needed.

- **Model**: ElevenLabs Flash 2.5 (`eleven_flash_v2_5`) — generates in ~1-3 seconds.
- **Storage**: MP3 files stored in S3 at `audio/{world_id}/{node_id}.mp3`, served via the existing CloudFront CDN. URLs are permanent and publicly accessible (no signed URLs needed).
- **Idempotency**: If audio already exists for a node, the endpoint returns the cached URL instantly without consuming quota.
- **Race protection**: Concurrent requests for the same node are safe — only the first generation consumes quota.

### Tier Limits

| Tier       | Audio narrations per period | Period  | Reset behaviour                    |
| ---------- | --------------------------- | ------- | ---------------------------------- |
| FREE       | 20                          | 7 days  | **Never resets** (lifetime cap)    |
| EXPLORER   | 60                          | 30 days | Resets each billing period         |
| COSMONAUT  | 200                         | 30 days | Resets each billing period         |

> Free-tier audio is a one-time allowance — once the 20 narrations are used, the user must upgrade to generate more. Paid tiers get fresh quota each billing cycle.

---

## API Reference

### Generate Audio for a Node

```
POST /worlds/{world_id}/nodes/{node_id}/audio
```

**Auth**: Requires a valid JWT (same as all `/worlds` endpoints).

**Preconditions**:
- The node must exist.
- The node's `generation_status` must be `"completed"` (i.e., text has been fully generated). If text is still streaming or hasn't been generated, the endpoint returns `400`.

**Request body**: None.

**Success response** (`200 OK`):

```json
{
  "audio_url": "https://images.dev.cosmonaut-ai.com/audio/{world_id}/{node_id}.mp3"
}
```

The `audio_url` is a permanent CDN link to the MP3 file. It can be used directly in an HTML `<audio>` element.

**Error responses**:

| Status | Condition                             | Response `detail`                                  |
| ------ | ------------------------------------- | -------------------------------------------------- |
| `400`  | Node text not yet generated           | `"Node {node_id} text has not been generated yet"` |
| `403`  | User not authorized for this world    | `"You are not authorized to access world ..."`     |
| `404`  | World or node not found               | `"World {id} not found"` / `"Node {id} not found"` |
| `429`  | Audio quota exceeded                  | `"Quota exceeded: audio limit is {limit}"`         |
| `500`  | ElevenLabs or S3 failure              | `"Audio generation failed"`                        |

> The `429` response is the key one for triggering the upgrade prompt on the frontend.

---

### Get Usage Info (Updated)

```
GET /auth/usage
```

The existing usage endpoint now includes two new fields:

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

New fields:
- `audio_narrations_used` — how many audio narrations the user has generated this period (or lifetime for free tier).
- `audio_narrations_limit` — the user's tier cap.

---

### Story Node DTO (Updated)

All endpoints that return a `StoryNodeDTO` (`GET /worlds/{id}/nodes/`, `GET /worlds/{id}/nodes/{id}`, etc.) now include two new optional fields:

```json
{
  "id": "0a",
  "world_id": "abc123",
  "text": "The ancient door creaked open...",
  "title": "The Threshold",
  "generation_status": "completed",
  "audio_url": "https://images.dev.cosmonaut-ai.com/audio/abc123/0a.mp3",
  "audio_voice_id": "pNInz6obpgDQGcFmaJgB",
  "...": "..."
}
```

- `audio_url` — `string | null`. CDN URL of the generated MP3. `null` if audio has not been generated for this node.
- `audio_voice_id` — `string | null`. The ElevenLabs voice ID used. Included for future features (e.g., per-world voice selection). Can be ignored by the frontend for now.

---

## Frontend Implementation Guide

### 1. API Client

Add a function to call the new audio endpoint:

```typescript
// src/lib/api/client.ts
async function generateNodeAudio(worldId: string, nodeId: string): Promise<{ audio_url: string }> {
  const response = await fetch(`${API_BASE}/worlds/${worldId}/nodes/${nodeId}/audio`, {
    method: 'POST',
    headers: getAuthHeaders(),
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

### 2. Tier Configuration

Update `src/lib/config/tiers.ts` (or equivalent) to display audio limits in the pricing/features UI:

| Tier       | Display text                  |
| ---------- | ----------------------------- |
| FREE       | "20 audio narrations"         |
| EXPLORER   | "60 audio narrations / month" |
| COSMONAUT  | "200 audio narrations / month"|

### 3. Node View Integration

In `StoryNodeView.svelte` (or equivalent component):

**State**:
- `isGeneratingAudio: boolean` — true while the POST request is in flight.
- Use the node's `audio_url` from the DTO to determine if audio already exists.

**UI**:
- Add a narration button (e.g., speaker/Volume2 icon) near the existing controls.
- **Disable** the button when:
  - Text is still streaming (`isStreaming` or `isNodeGenerating`)
  - `generation_status !== "completed"`
  - `isGeneratingAudio` is true (show a spinner/loading state instead)

**Behaviour**:

```
if node.audio_url exists:
    → Play/pause toggle using <audio> element
else:
    → Call generateNodeAudio(worldId, nodeId)
    → On success: update node.audio_url in local state, begin playback
    → On 429 error: show UpgradePrompt (reuse existing quota-exceeded pattern)
    → On other error: show toast/error message
```

**Playback**:
- Use a standard HTML `<audio>` element or a Svelte audio binding.
- The CDN URL can be set as the `src` directly — no auth headers needed for playback.
- Consider preloading: `<audio preload="none">` to avoid unnecessary bandwidth on page load.

### 4. Usage Display

The `/auth/usage` response now includes `audio_narrations_used` and `audio_narrations_limit`. Display this alongside existing usage meters (nodes, worlds) wherever the user can see their current consumption, e.g.:

```
Audio narrations: 12 / 60
```

### 5. Clean Up

The existing browser-native `window.speechSynthesis` implementation in `StoryNodeView.svelte` should be removed or hidden behind a fallback option, since ElevenLabs audio is now the primary narration method.

---

## Sequence Diagram

```
User clicks "Play Audio" on a completed story node
  │
  ├─ node.audio_url exists?
  │   ├─ YES → Play the MP3 directly (no API call)
  │   └─ NO  → POST /worlds/{worldId}/nodes/{nodeId}/audio
  │              │
  │              ├─ 200 → { audio_url: "https://cdn.../audio/..." }
  │              │         Update local node state, begin playback
  │              │
  │              ├─ 429 → Quota exceeded → Show UpgradePrompt
  │              │
  │              ├─ 400 → Node text not ready (shouldn't happen if button is disabled properly)
  │              │
  │              └─ 500 → Generation failed → Show error toast
```

---

## Notes

- Audio generation takes ~1-3 seconds. The UI should show a loading/spinner state during this time.
- The returned `audio_url` is permanent — once generated, it never expires or changes. The frontend can cache it freely.
- The endpoint is idempotent: calling it multiple times for the same node always returns the same URL and only consumes quota once.
- Audio files are MP3 format, typically 100-300KB for a story node.
