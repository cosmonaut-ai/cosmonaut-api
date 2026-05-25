# Cosmonaut AI: Technical Design Specification

**Version:** 1.3
**Date:** May 25, 2026
**Owner:** Matson Software LLC

## 1. Executive Summary

Cosmonaut AI is a "Choose Your Own Adventure" platform utilizing Generative AI. It differentiates itself via **Narrative Consistency**, using a graph-based state machine, lineage-filtered vector memory, and rolling summaries to ensure the story remains coherent across infinite branching paths.

## 2. Infrastructure Architecture (AWS + Terraform)

The infrastructure is fully defined in Terraform (`cosmonaut-infra`) and deployed via GitHub Actions using OIDC authentication.

- **Compute:** AWS Lambda (Python 3.13) behind API Gateway HTTP API (v2).
- **Database:** Amazon DynamoDB (On-Demand Capacity).
- **Vector Store:** Pinecone (Serverless).
- **Hosting:** AWS S3 + CloudFront (OAC Secured).
- **Secrets:** AWS SSM Parameter Store.

---

## 3. Data Architecture (DynamoDB Schema)

- **Table Name:** `cosmonaut-${var.env}`
- **Billing Mode:** `PAY_PER_REQUEST`
- **Partition Key (PK):** `PK` (String)
- **Sort Key (SK):** `SK` (String)

### 3.1 Entity Models

#### A. The World Container (Metadata)

- **PK:** `WORLD#<uuid>`
- **SK:** `META`
- **Attributes:** `Title`, `AuthorID`, `RootNodeID`, `Visibility`, `GSI_Genre_PK`, `GSI_Score_SK`.

#### B. The Story Node (Static Content)

- **PK:** `WORLD#<uuid>`
- **SK:** `NODE#<ulid>`
- **Attributes:**
- `Text` (String): The raw narrative content of this specific node.
- `StorySummary` (String) **[NEW]**: A running summary of the story _up to and including_ this node.
- _Logic:_ `Parent.StorySummary` + `Node.Text` -> LLM Summarizer -> `Node.StorySummary`.

- `Choices` (List<Map>): `[{ "label": "...", "target": "NODE#..." }]`
- `ParentID` (String)
- `Ancestors` (List<String>)
- `Coordinates` (Map)

#### C. The User Progress (Save State)

- **PK:** `USER#<cognito_sub_id>`
- **SK:** `PROGRESS#<world_uuid>`
- **Attributes:** `CurrentNodeID`, `LastPlayed`, `WorldTitle`.

### 3.2 Indexes (Unchanged)

- `VisualGraphIndex` (Graph UI), `DiscoveryIndex` (Public Feed), `OwnerIndex` (My Stories).

---

## 4. Vector Memory Strategy (Pinecone)

We utilize a single Pinecone Index partitioned by **Metadata Filters** to handle three distinct types of memory.

- **Secrets Path:** `/<env>/cosmonaut/pinecone_api_key`
- **Metric:** Cosine Similarity.

### 4.1 Vector Entities & Schema

Every vector stored includes the following metadata fields:

| Field            | Description                                            |
| ---------------- | ------------------------------------------------------ |
| `world_id`       | The Partition Key of the story.                        |
| `entity_type`    | `node_text`                                            |
| `origin_node_id` | The specific node ID where this content was generated. |

#### Type 1: Story Nodes (`entity_type="node_text"`)

- **Content:** The raw text of past nodes.
- **Purpose:** Allows the LLM to recall specific phrasing or events from earlier in the user's specific path.
- **Retrieval Filter:** `world_id` match + `origin_node_id` **IN** `CurrentNode.Ancestors`.

#### Type 2: World Facts (`entity_type="world_fact"`)

- **Content:** Extracted static truths (e.g., "The sky is purple," "Gravity is weak").
- **Purpose:** Global consistency. These facts apply to _all_ branches.
- **Retrieval Filter:** `world_id` match. (No Ancestor filter required, or implicitly linked to Root).

#### Type 3: Branch Facts (`entity_type="branch_fact"`)

- **Content:** State-dependent truths (e.g., "The King is dead," "You have the Key").
- **Purpose:** Branch-specific consistency.
- **Retrieval Filter:** `world_id` match + `origin_node_id` **IN** `CurrentNode.Ancestors`.

---

## 5. The "Generation Loop" (Backend Logic)

When generating a new node, the Lambda follows this strict sequence to ensure context is maintained without exceeding token limits.

### Step 1: Gather Context

The Lambda constructs a prompt using three sources:

1. **Immediate Context:** `CurrentNode.Text` + `CurrentNode.StorySummary`.
2. **Vector Context (RAG):** Query Pinecone for the user's prompt (e.g., "Open the chest") using the filters defined in 4.1.

- _Fetch:_ Top 3 related `node_text` chunks (from Ancestors only).
- _Fetch:_ Top 3 related `branch_facts` (from Ancestors only).
- _Fetch:_ Top 3 related `world_facts` (Global).

### Step 2: Generate Narrative

**LLM Call 1 (The Storyteller):**

- **Input:** Summary + Retrieved Context + User Choice.
- **Output:** New Narrative Text + List of Choices.

### Step 3: Mutate Summary

**LLM Call 2 (The Archivist):** (Can be parallel or chained)

- **Input:** `Parent.StorySummary` + `New Node Text`.
- **Prompt:** "Update the story summary to include the events of this new paragraph, keeping it under 300 words."
- **Output:** `NewNode.StorySummary`.

### Step 4: Extract Facts

**LLM Call 3 (The Analyst):**

- **Input:** `New Node Text`.
- **Output:** List of new facts (if any).
- **Action:**
- Embed & Upsert to Pinecone as `branch_fact` (linked to `NewNode.ID`).
- _Optional:_ If high confidence of global truth, upsert as `world_fact`.

### Step 5: Persist

- **DynamoDB:** Save `NODE#<ulid>` with the `Text`, `Choices`, `Ancestors`, and the new `StorySummary`.
- **DynamoDB:** Update `USER#...` progress pointer.

---
