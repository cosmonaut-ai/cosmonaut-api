# Frontend Guide: World Length & Family-Friendly Options

Two new optional fields have been added to the **Create World** request and are returned on every **World** response.

---

## New Fields

### `world_length`

Controls how long a single story branch can be (max number of nodes before the LLM starts wrapping up the narrative).

| Value      | Max Nodes | Approx. Read Time |
| :--------- | :-------- | :----------------- |
| `"short"`  | 5         | ~5 minutes         |
| `"medium"` | 10        | ~10 minutes        |
| `"long"`   | 15        | ~20 minutes        |

- **Type:** `string` enum — `"short"` | `"medium"` | `"long"`
- **Default:** `"medium"` (if omitted from the request)
- **Immutable after creation** — this value is set once and determines `story_max_nodes` for the world.

### `family_friendly`

When enabled, all LLM-generated content for the world (lore, narrator voice, and every story node) is constrained to be child-safe: no graphic violence, no profanity, no horror, approachable vocabulary, and an encouraging tone.

- **Type:** `boolean`
- **Default:** `false` (if omitted from the request)
- **Immutable after creation** — the flag is baked into the world's generated lore and narrator profile, so changing it retroactively would create inconsistencies.

---

## API Changes

### `POST /worlds/` — Create a World

Two new optional fields in the request body:

```json
{
  "world_prompt": "A steampunk city where clockwork robots are gaining sentience.",
  "visibility": "private",
  "world_length": "short",
  "family_friendly": true
}
```

Both fields are optional; omitting them gives `"medium"` and `false` respectively.

### `GET /worlds/{world_id}` and `GET /worlds/` — Read Worlds

The response now includes both fields on every `WorldMetaDTO`:

```json
{
  "id": "uuid-123",
  "title": "The Gilded Gear",
  "generation_status": "completed",
  "story_max_nodes": 5,
  "world_length": "short",
  "family_friendly": true,
  "...": "..."
}
```

- `world_length` — the preset chosen at creation (`"short"`, `"medium"`, or `"long"`)
- `family_friendly` — `true` or `false`
- `story_max_nodes` — the resolved integer max depth (derived from `world_length`; already existed but was previously hardcoded)

---

## UI Recommendations

### World Creation Form

1. **World Length** — a segmented control or radio group with three options:
   - Short (~5 min read)
   - Medium (~10 min read) — pre-selected as default
   - Long (~20 min read)

2. **Family Friendly** — a toggle/switch, off by default. Consider a short helper label like _"Make this story suitable for kids"_.

### World Detail / Card

Both values can be displayed as badges or metadata on world cards:
- World length as a label (e.g., "Short story" / "Medium story" / "Long story")
- Family-friendly as an icon or badge when `true`

---

## Notes

- These fields are **creation-time only**. They are not accepted by `PATCH /worlds/{world_id}`.
- Existing worlds (created before this change) will have `world_length: null` and `family_friendly: false` in their responses.
- `story_max_nodes` on the response reflects the resolved value and can be used directly if you need the raw number for progress indicators.
