# Project Description

Cosmonaut AI is an interactive storytelling platform where users create and explore branching, text-based adventures generated with large language models.

Each story begins with a user-provided world prompt and continues through a sequence of choices. The story graph is represented as worlds and story nodes: each node contains narrative text, available choices, parent/child relationships, and metadata used for traversal, sharing, and regeneration.

The core product requirement is narrative consistency. Facts established in a world should remain coherent as users move through branches, backtrack, and explore alternative paths. The backend supports this through a combination of structured story-node state, rolling summaries, lineage-aware context, and vector memory.

The platform is designed around three long-term user experiences:

- Create: start a new world from a prompt, preset genre, or guided creation flow.
- Explore: continue generated stories through choices, audio narration, and graph traversal.
- Share: make story worlds discoverable so other users can replay existing branches or generate new ones from the same foundation.

The current implementation focuses on single-player interactive stories, with room for future collaboration or multiplayer features.
