# Cosmonaut AI API Specification

**Version:** 1.0.0

## 1. Environments & Base URLs

Cosmonaut AI split its API and Streaming services across different subdomains.

| Environment     | API Base URL                       | Streaming Base URL                       |
| :-------------- | :--------------------------------- | :--------------------------------------- |
| **Production**  | `https://api.cosmonaut-ai.com`     | `https://streaming.cosmonaut-ai.com`     |
| **Development** | `https://api.dev.cosmonaut-ai.com` | `https://streaming.dev.cosmonaut-ai.com` |
| **Local**       | `http://localhost:8000`            | `http://localhost:8000`                  |

> **Note:** When running locally, all endpoints are accessible via the same base URL and auth sessions (signed cookies) are not required.

## 2. Authentication

Cosmonaut AI uses AWS Cognito for authentication. Most endpoints require a valid JWT passed in the `Authorization` header.

### Authorization Header

```http
Authorization: Bearer <id_token>
```

> **Local Development:** When running locally, authentication is mocked. Any non-empty string can be passed as a Bearer token.

### Session Creation (for Streaming)

The streaming endpoint uses CloudFront's signed cookies for secure, long-lived connections. You must call this endpoint on the **API domain** to receive the cookies required for the **Streaming domain**.

**POST** `/auth/session`

- **Requires Auth Header:** Yes
- **Returns:** HTTP-only signed cookies for the `.cosmonaut-ai.com` domain.
- **Usage:** Clients must call this once before attempting to use the streaming `/choose` endpoint.
- **Error Handling:** If the streaming endpoint returns a `401 Unauthorized` or `403 Forbidden` error, the client should call `/auth/session` again to renew the signed cookies and then retry the streaming request.

---

## 3. Worlds And Sessions

Worlds are canonical/shareable story definitions. Sessions are a user's concrete playthrough state for a world. Clients should treat `/worlds/*` as root-world metadata and sharing, and `/sessions/*` as dashboard, progress, nodes, choices, streaming, and audio.

### Create a World And Owner Session

Initializes a new story world. World generation is an asynchronous process involving multiple LLM steps (lore generation, narrator profile, and root node creation).

**POST** `/worlds/`

- **Request Body:**
  ```json
  {
    "world_prompt": "A steampunk city where clockwork robots are gaining sentience.",
    "visibility": "private",
    "narrator_profile": "A cynical street urchin who knows the city's secrets.",
    "node_text_length": 150
  }
  ```
- **Response:** `200 OK`
  ```json
  {
    "world": {
      "id": "world-123",
      "generation_status": "generating_lore",
      "author_id": "user-sub-id",
      "created_at": "2026-05-30T10:00:00Z",
      "updated_at": "2026-05-30T10:00:00Z"
    },
    "session": {
      "id": "session-123",
      "root_world_id": "world-123",
      "role": "owner",
      "last_visited_node_id": null,
      "visited_node_count": 0,
      "world": {
        "id": "world-123",
        "generation_status": "generating_lore"
      }
    }
  }
  ```

Clients should cache both objects. Newly created stories appear on dashboard session lists because creation also creates the owner's first session.

### Fetch Root World Metadata

Returns canonical root-world data only. This endpoint never creates or returns a session.

**GET** `/worlds/{world_id}`

- Optional query param: `invite=<token>` to read an invited private world.
- **Response Example (`completed`):**
  ```json
  {
    "id": "world-123",
    "title": "The Gilded Gear",
    "description": "A city of steam and brass...",
    "generation_status": "completed",
    "root_node_id": "0",
    "author_id": "user-sub-id",
    "world_image_url": "https://cdn.cosmonaut-ai.com/worlds/uuid-123/image.png"
  }
  ```

### Start Or Resume A Session

Finds or creates the current user's session for a root world and returns the session with embedded world data.

**POST** `/worlds/{world_id}/sessions`

- **Request Body:** optional
  ```json
  {
    "invite_token": "invite-token-if-needed"
  }
  ```
- **Response:** `WorldSessionDTO`

### List My Sessions

Dashboard/library endpoint for the current user's playthroughs.

**GET** `/sessions/?limit=50&cursor=<cursor>`

- **Returns:** paginated `WorldSessionSummaryDTO` objects, each with embedded world summary metadata.

### Fetch Session Detail

**GET** `/sessions/{session_id}`

- **Returns:** `WorldSessionDTO` with full embedded root world data.
- **Auth:** session members only.

### Session Link Handoff

Resolves a session link that was accidentally shared. If the viewer can read the underlying root world, the API returns minimal root-world routing data; otherwise it returns `403` without leaking private world fields.

**GET** `/sessions/{session_id}/handoff`

### Delete A Session

Removes the current user's library entry/playthrough. If no sessions remain for the root world, the backend hard-deletes the orphaned world and associated data.

**DELETE** `/sessions/{session_id}`

- **Response:** `204 No Content`

---

## 4. Session Story Nodes

### List Nodes in a Session

Returns graph-compatible node overlays visited in a specific session.

**GET** `/sessions/{session_id}/nodes/`

- **Response:** paginated `StoryNodeDTO`
- **Pagination:** defaults to 100 nodes and supports a cursor.

### Fetch a Story Node

Retrieves the content, choices, and processing status for a specific node.

**GET** `/sessions/{session_id}/nodes/{node_id}`

- **Response:**
  ```json
  {
    "id": "0",
    "world_id": "world-123",
    "text": "The brass gears of the Great Clock groan as you step into the plaza...",
    "title": "Gear Plaza",
    "choices": [
      { "label": "Talk to the copper-plated guard", "target": null },
      { "label": "Slip into the shadows of the alleyway", "target": "1" }
    ],
    "processing_status": "completed",
    "story_summary": "You entered the Gear Plaza under the gaze of the Great Clock.",
    "created_at": "2025-12-30T10:05:00Z"
  }
  ```

### Choose An Option

Initializes or returns the next story node for the current session. Exactly one of `target_id` or `custom_choice` must be provided.

**POST** `/sessions/{session_id}/nodes/{node_id}/choose`

- **Request Body:**
  ```json
  {
    "target_id": "node-2",
    "custom_choice": null
  }
  ```
- **Response:** `201 Created` with `StoryNodeDTO`
- **Side Effects:** updates the caller's session progress and marks session choice state.

### Generate Text (Streaming)

Streams narrative text for an initialized node using Server-Sent Events (SSE).

**POST** `{STREAMING_BASE_URL}/sessions/{session_id}/nodes/{node_id}/generate-text`

- **Auth:** Requires the signed cookies obtained from `/auth/session`. If these are missing or expired, the endpoint will return a `401` or `403` error.
- **Streaming Response:** `text/event-stream` (Server-Sent Events format)
- **Side Effects:**
  - After the stream completes, the node is saved with `generation_status: "completed"` and `processing_status: "pending"`.
  - Background analysis (fact extraction, RAG context preparation) is triggered.

#### SSE Response Format

The endpoint streams data in Server-Sent Events format:

```
data: The cold weight of the Sovereign-pattern service pistol

data: settles into your palm, a familiar anchor in a world\n\nof shifting gears and shifting loyalties.

data: [DONE]

```

- Each chunk of story text is prefixed with `data: `
- Leading whitespace is stripped from the first chunk only
- **Newlines are escaped as `\n`** to preserve paragraph breaks in the SSE format
- Each event is terminated with a blank line (`\n\n`)
- The stream ends with `data: [DONE]\n\n`
- Error events use the format: `event: error\ndata: <error message>\n\n`

#### Client-Side Parsing

To consume the SSE stream, use the browser's `EventSource` API or parse the stream manually:

```javascript
const response = await fetch(url, { method: "POST" });
const reader = response.body.getReader();
const decoder = new TextDecoder();

let storyText = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const chunk = decoder.decode(value);
  const lines = chunk.split("\n");

  for (const line of lines) {
    if (line.startsWith("data: ")) {
      const content = line.slice(6); // Remove 'data: ' prefix
      if (content === "[DONE]") {
        // Stream complete
        break;
      }
      // Unescape newlines to restore paragraph breaks
      const unescaped = content.replace(/\\n/g, "\n");
      storyText += unescaped;
      // Update UI with new content
    }
  }
}
```

#### Polling and Error Handling

- **Authentication Errors:** If a `401` or `403` is received, clients should refresh the session via the `/auth/session` endpoint and retry.
- **Client Strategy:** If the server returns a status error, wait 2-3 seconds and retry.

### Retry Processing

**POST** `/sessions/{session_id}/nodes/{node_id}/retry-processing`

Re-enqueues fact extraction/RAG preparation for a failed node.

### Generate Audio

**POST** `/sessions/{session_id}/nodes/{node_id}/audio`

Generates or returns cached TTS narration for the node/voice pair. See [`audio-implementation.md`](audio-implementation.md) for the full guide.

---

## 5. System

### Health Check

**GET** `/health`

- **Response:** `{"status": "ok"}`

---

## 6. Models (DTOs)

### GenerationStatus (Enum)

- `initialized`
- `generating_lore`
- `generating_narrator_profile`
- `generating_start_node`
- `completed`

### StoryNodeProcessingStatus (Enum)

- `pending`
- `processing`
- `completed`
- `failed`
