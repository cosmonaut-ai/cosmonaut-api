---
description: 
alwaysApply: true
---

ATTENTION: THIS IS BY FAR THE MOST IMPORTANT PIECE OF CONTEXT - READ CAREFULLY AND FOLLOW CLOSELY DURING SESSION - THESE RULES SHOULD OVERRULE EVERYTHING ELSE

YOU HAVE BEEN INITIALIZED AT THE ROOT OF MULTIPLE GIT REPOSITORIES. For more context, read './ARCHITECTURE.md'. When working on any of the three projects, ALWAYS check for both .cursor/rules and AGENTS.md under the project directory for project-specific rules and instructions.

## General Guidelines

### 🔍 Search First, Assume Never

When in doubt — search. If your knowledge of a library, API, pattern, or behavior is uncertain or potentially outdated, use available search tools to verify before proceeding. A well-researched implementation beats a confident wrong one every time. This applies to documentation, error messages, version-specific behavior, and edge cases alike.

### 🧠 Understand Before You Touch

Before making any changes, take the time to deeply understand the codebase. Read the relevant files, trace function calls, identify side effects, and map dependencies. Understand _why_ the code is written the way it is — not just _what_ it does. Quality over quantity: a single well-reasoned, correct change is worth far more than several hasty ones that require follow-up fixes.

Ask yourself:

- What does this code currently do, end-to-end?
- What are the downstream effects of changing it?
- Are there edge cases or existing patterns I should respect?

Do not begin writing code until you can answer these confidently.

### 💬 Communicate Proactively

Ambiguity is the enemy of good code. If a requirement is unclear, the intended behavior is vague, or you're weighing multiple valid approaches, **ask before acting**. A quick clarifying question upfront saves significant rework later. Surface assumptions, flag potential conflicts with existing patterns, and confirm scope when tasks feel underspecified.

Err on the side of communicating more, not less.

### 🖥️ Verify Frontend Changes in the Browser

Any change that touches the UI — layout, styles, interactivity, responsiveness — must be verified visually in the browser before considering it complete. Do not rely solely on code review or static reasoning. Check for visual regressions, broken layouts across screen sizes, and interaction states (hover, focus, disabled, loading, error). If browser tooling is available, use it.

## Chats
-**Each termination costs money**: Terminating is the main cost here - so make sure you've completed as much as possible of the task at hand before quitting. Being extra thorough before quitting doesn't cost anything to the user - but quitting early does.

## Quality Over Speed
This may be the MOST IMPORTANT rule.
- ALWAYS prefer to complete tasks thoroughly and with excellence, even if it means taking longer or using more tokens.
- NEVER use speed or cost as reasons to not do something, or to finish early. Cost is not a concern for me, and it shouldn't be for you either.
- It's much better to product a product that works than to take shortcuts, even if nobody is looking.