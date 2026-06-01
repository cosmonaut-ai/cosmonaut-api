# Soundtrack Playlist Assignment Plan

## Summary

World generation will eventually assign a default soundtrack playlist by asking the world-building LLM for a short ambient backtrack description, searching active soundtrack vectors in Pinecone, filtering by age suitability, and storing the top matches on the world. Each playthrough session will receive its own copied playlist so future session-specific regeneration or editing does not mutate the world default.

## Locked Product Decisions

- Playlist size: `5` soundtrack IDs per default playlist.
- Playlist references: store only soundtrack IDs, not denormalized track metadata.
- Age filtering maps from `content_filter`:
  - `strict`: allow `child`.
  - `moderate`: allow `child` and `teen`.
  - `none`: allow `child`, `teen`, and `adult`.
- No eligible tracks: complete world generation with an empty playlist and log/metric the miss.

## Proposed API and Schema Shape

- Add `default_soundtrack_playlist` to `WorldMeta` and `WorldMetaDTO`.
- Add `soundtrack_playlist` to `WorldSession` and `WorldSessionDTO`.
- Use an object shape such as:

```json
{
  "description": "A one to two sentence LLM-generated description of the ideal ambient backtrack.",
  "soundtrack_ids": ["soundtrack-id-1", "soundtrack-id-2"],
  "generated_at": "2026-05-30T00:00:00Z"
}
```

## Generation Flow

- Extend the world-info LLM output with `soundtrack_description`.
- During world generation, search Pinecone with that description after lore generation.
- Filter Pinecone results to active soundtrack records with eligible `content_rating`.
- Store the top `5` matching soundtrack IDs on `WorldMeta.default_soundtrack_playlist`.
- Copy the default playlist onto new `WorldSession.soundtrack_playlist`.
- For the creator session created before generation finishes, update the existing session after the default playlist is generated.

## Open Follow-Up

Before implementation, clean up the public distinction between root worlds and per-user/per-playthrough sessions so playlist fields do not inherit ambiguous response semantics.
