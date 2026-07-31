# Frontend Guide: World Creation Options

Optional fields on the **Create World** request, returned on every **World** response.

---

## Fields

### `world_length`

Controls how long a single story branch can be (max number of nodes before the LLM starts wrapping up the narrative).

| Value      | Max Nodes | Approx. Read Time |
| :--------- | :-------- | :---------------- |
| `"short"`  | 5         | ~5 minutes        |
| `"medium"` | 10        | ~10 minutes       |
| `"long"`   | 15        | ~20 minutes       |

- **Type:** `string` enum — `"short"` | `"medium"` | `"long"`
- **Default:** `"medium"` (if omitted from the request)
- **Immutable after creation** — this value is set once and determines `story_max_nodes` for the world.

### `vocab_level`

Controls the vocabulary complexity and reading level used in generated story text.

| Value     | Description                                            |
| :-------- | :----------------------------------------------------- |
| `"child"` | Simple words and short sentences suitable for ages 8+. |
| `"teen"`  | Moderate vocabulary complexity for ages 13+.           |
| `"adult"` | No vocabulary restrictions.                            |

- **Type:** `string` enum — `"child"` | `"teen"` | `"adult"`
- **Default:** `"adult"` (if omitted from the request)
- **Immutable after creation** — the vocab level is baked into the world's generated lore and narrator profile.

### `content_filter`

Controls how explicit graphic material (mainly violence) can be. Sexual content is never allowed on the platform regardless of this setting.

| Value        | Description                                                    |
| :----------- | :------------------------------------------------------------- |
| `"none"`     | No content filtering beyond platform rules.                    |
| `"moderate"` | Violence without gratuitous detail. No extreme horror.         |
| `"strict"`   | No graphic violence, profanity, horror, or disturbing imagery. |

- **Type:** `string` enum — `"none"` | `"moderate"` | `"strict"`
- **Default:** `"none"` (if omitted from the request)
- **Immutable after creation** — the filter level is baked into the world's generated lore and narrator profile.

---

## API Changes

### `POST /worlds/` — Create a World And Owner Session

Optional fields in the request body:

```json
{
  "world_prompt": "A steampunk city where clockwork robots are gaining sentience.",
  "visibility": "private",
  "world_length": "short",
  "vocab_level": "teen",
  "content_filter": "strict"
}
```

All fields except `world_prompt` are optional; omitting them gives `"medium"`, `"adult"`, and `"none"` respectively.

The response contains both the root world and the owner's initial playthrough session:

```json
{
  "world": {
    "id": "uuid-123",
    "generation_status": "generating_lore",
    "story_max_nodes": 5,
    "world_length": "short",
    "vocab_level": "teen",
    "content_filter": "strict"
  },
  "session": {
    "id": "session-123",
    "root_world_id": "uuid-123",
    "role": "owner",
    "world": {
      "id": "uuid-123",
      "generation_status": "generating_lore",
      "story_max_nodes": 5,
      "world_length": "short",
      "vocab_level": "teen",
      "content_filter": "strict"
    }
  }
}
```

### `GET /worlds/{world_id}` and session responses — Read Worlds

`GET /worlds/{world_id}` returns canonical root-world metadata only. `GET /sessions/` and `GET /sessions/{session_id}` embed world metadata on each session. In both cases, the embedded `WorldMetaDTO` includes:

```json
{
  "id": "uuid-123",
  "title": "The Gilded Gear",
  "generation_status": "completed",
  "story_max_nodes": 5,
  "world_length": "short",
  "vocab_level": "teen",
  "content_filter": "strict",
  "...": "..."
}
```

---

## UI Recommendations

### World Creation Form

1. **World Length** — a segmented control with three options (Short / Medium / Long).
2. **Vocabulary Level** — a segmented control with three options (Child / Teen / Adult), default Adult. Each option has a tooltip.
3. **Content Filter** — a segmented control with three options (None / Moderate / Strict), default None. Each option has a tooltip.

### World Home Page

- Show `vocab_level` badge when not `"adult"` and `content_filter` badge when not `"none"`.

### World Card

- Show a tiered shield icon for content filter: plain shield for moderate, shield-plus for strict, no icon for none.

---

## Notes

- These fields are **creation-time only**. They are not accepted by `PATCH /worlds/{world_id}`.
- Existing worlds migrated from the legacy `family_friendly` flag will have `vocab_level: "teen"` and `content_filter: "strict"` if they were family-friendly, otherwise `"adult"` and `"none"`.
- `story_max_nodes` on the response reflects the resolved value and can be used directly for progress indicators.
