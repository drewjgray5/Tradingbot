# /raw — Immutable Source Documents

This folder holds **original, unmodified source material** that feeds the wiki.

## Rules

1. **Never edit files in this folder.** They are the single source of truth.
2. **Supported formats:** `.md`, `.txt`, `.pdf`, `.py`, `.json`, `.csv`, `.html`, or any plaintext.
3. **Naming convention:** Use descriptive, slugified names — e.g., `schwab-api-docs.md`, `karpathy-llm-patterns.txt`.
4. **Subdirectories are fine** — organize by topic if the folder grows large.

## How it works

When you run the **Ingest** command (defined in `CLAUDE.md`), the LLM reads documents from this folder, extracts key concepts, decisions, and facts, then compiles them into interlinked wiki pages in `/wiki`.

Drop files here and run Ingest to grow the wiki.
