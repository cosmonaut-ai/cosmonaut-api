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

## 3. Worlds

### Create a World (Asynchronous)

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
    "id": "uuid-123",
    "generation_status": "generating_lore",
    "author_id": "user-sub-id",
    "created_at": "2025-12-30T10:00:00Z",
    "updated_at": "2025-12-30T10:00:00Z"
  }
  ```

### Polling World Status

Clients should poll this endpoint until `generation_status` reaches `completed`.

**GET** `/worlds/{world_id}`

- **Response Example (`completed`):**
  ```json
  {
    "id": "uuid-123",
    "title": "The Gilded Gear",
    "description": "A city of steam and brass...",
    "generation_status": "completed",
    "root_node_id": "0",
    "author_id": "user-sub-id",
    "world_image_url": "https://cdn.cosmonaut.ai/worlds/uuid-123/image.png"
  }
  ```

### List My Worlds

**GET** `/worlds/`

- **Returns:** A list of `WorldMetaDTO` objects for the authenticated user.

### Delete a World

**DELETE** `/worlds/{world_id}`

- **Response:** `204 No Content`
- **Behavior:** Deletes the world metadata, all associated story nodes, and clears vector memory from Pinecone.

---

## 4. Story Nodes

### List Nodes in a World

Returns generated nodes for a specific world.

**GET** `/worlds/{world_id}/nodes/`

- **Response:** `list[StoryNodeDTO]`
- **Pagination:** Currently returns up to 100 nodes.

### Fetch a Story Node

Retrieves the content, choices, and processing status for a specific node.

**GET** `/worlds/{world_id}/nodes/{node_id}`

- **Response:**
  ```json
  {
    "id": "0",
    "world_id": "uuid-123",
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

### Choose and Generate (Streaming)

Selects a choice and streams the narrative text of the next node using Server-Sent Events (SSE).

**POST** `{STREAMING_BASE_URL}/worlds/{world_id}/nodes/{node_id}/choose/{choice_index}`

- **Note:** If the choice already has a `target` ID (pre-generated), the API will return the full text immediately.
- **Auth:** Requires the signed cookies obtained from `/auth/session`. If these are missing or expired, the endpoint will return a `401` or `403` error.
- **Streaming Response:** `text/event-stream` (Server-Sent Events format)
- **Side Effects:**
  - After the stream completes, the new node is saved with `processing_status: "pending"`.
  - Background analysis (fact extraction, RAG context preparation) is triggered.

#### SSE Response Format

The endpoint streams data in Server-Sent Events format:

```
data: The cold weight of the Sovereign-pattern service pistol

data: settles into your palm, a familiar anchor in a world

data: of shifting gears and shifting loyalties.

data: [DONE]

```

- Each chunk of story text is prefixed with `data: `
- The stream ends with `data: [DONE]\n\n`
- Error events use the format: `event: error\ndata: <error message>\n\n`

#### Client-Side Parsing

To consume the SSE stream, use the browser's `EventSource` API or parse the stream manually:

```javascript
const response = await fetch(url, { method: 'POST' });
const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const chunk = decoder.decode(value);
  const lines = chunk.split('\n');

  for (const line of lines) {
    if (line.startsWith('data: ')) {
      const content = line.slice(6); // Remove 'data: ' prefix
      if (content === '[DONE]') {
        // Stream complete
        break;
      }
      // Append content to story text
      storyText += content;
    }
  }
}
```

#### Polling and Error Handling

- **Authentication Errors:** If a `401` or `403` is received, clients should refresh the session via the `/auth/session` endpoint and retry.
- **Wait Time:** If you call `/choose` on a node that is still `pending` or `processing`, the server will wait up to **5 seconds** for it to complete before returning a `400 Bad Request` or `500 Internal Server Error`.
- **Client Strategy:** If the server returns a status error, wait 2-3 seconds and retry.

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
