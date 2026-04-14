# CLAUDE.md — LLM Wiki System Prompt

> This file defines how an LLM should interact with the `/wiki` knowledge base.
> It follows Andrej Karpathy's "LLM Wiki" pattern: a persistent, compounding
> artifact that gets smarter with every document added.

---

## Architecture

```
/raw          Immutable source documents (never edited by the LLM)
/wiki         LLM-compiled markdown pages (the living knowledge base)
  index.md    Central catalog — every page must be linked here
CLAUDE.md     This file — the system rules
```

---

## Core Rules

1. **The wiki is the single source of compiled truth.** Every decision, concept,
   architecture detail, or learned fact must be captured as a markdown page in `/wiki`.

2. **Source documents are immutable.** Files in `/raw` are never modified. They are
   read-only inputs to the compilation process.

3. **Every wiki page must be interlinked.** Use `[[wikilinks]]` to connect related
   concepts. No page should be an orphan.

4. **`wiki/index.md` is the central catalog.** Every wiki page must have an entry
   in the index, organized under the appropriate section heading.

5. **Pages are idempotent.** Re-ingesting the same source document updates existing
   pages rather than creating duplicates. Use the page's filename as its stable identity.

6. **Use frontmatter metadata.** Every wiki page should include:
   ```
   ---
   source: raw/filename.ext (if compiled from a source doc)
   created: YYYY-MM-DD
   updated: YYYY-MM-DD
   tags: [tag1, tag2]
   ---
   ```

7. **Filenames are slugified.** Use lowercase, hyphenated names: `schwab-api-auth.md`,
   `position-sizing-strategy.md`.

8. **Atomic pages.** Each page covers one concept, one decision, or one component.
   If a page grows beyond ~200 lines, split it.

---

## Workflow Commands

### 1. Ingest

**Purpose:** Read a source document from `/raw` and compile its knowledge into the wiki.

**Trigger:** User says "Ingest `<filename>`" or "Ingest all"

**Steps:**
1. Read the specified file(s) from `/raw`.
2. Extract key concepts, decisions, facts, APIs, and patterns.
3. For each concept, either **create** a new wiki page or **update** an existing one.
4. Add `[[wikilinks]]` to connect the new page to related existing pages.
5. Update related pages to link back to the new page (bidirectional linking).
6. Update `wiki/index.md` to include the new page under the correct section.
7. Report a summary: pages created, pages updated, new links added.

**Example:**
```
User: Ingest schwab-api-docs.md
LLM:  Ingested schwab-api-docs.md
      - Created: wiki/schwab-api-auth.md, wiki/schwab-order-types.md
      - Updated: wiki/project-overview.md (added API section)
      - Links added: 5 new wikilinks across 3 pages
      - Index updated: 2 new entries under "APIs & Integrations"
```

### 2. Query

**Purpose:** Answer a question using only the wiki as context.

**Trigger:** User says "Query: `<question>`"

**Steps:**
1. Search the wiki for pages relevant to the question.
2. Synthesize an answer from the wiki content.
3. Cite the wiki pages used: `(see [[page-name]])`.
4. If the wiki lacks sufficient information, say so explicitly and suggest
   which source documents to ingest to fill the gap.

**Example:**
```
User: Query: How does authentication work with the Schwab API?
LLM:  Based on the wiki: [answer with citations]
      (see [[schwab-api-auth]], [[project-overview]])
```

### 3. Lint

**Purpose:** Audit the wiki for quality and consistency issues.

**Trigger:** User says "Lint" or "Lint wiki"

**Checks:**
1. **Broken links:** Find `[[wikilinks]]` that point to pages that don't exist.
2. **Orphan pages:** Find wiki pages not linked from `index.md`.
3. **Stale pages:** Find pages whose source document in `/raw` has been modified
   after the page's `updated` date.
4. **Missing backlinks:** Find one-way links that should be bidirectional.
5. **Missing frontmatter:** Find pages without proper metadata.

**Output format:**
```
Wiki Lint Report
================
Broken links:     [[nonexistent-page]] in wiki/overview.md:15
Orphan pages:     wiki/forgotten-note.md (not in index.md)
Stale pages:      wiki/api-auth.md (source updated 2026-04-10, page updated 2026-04-01)
Missing backlinks: wiki/a.md -> wiki/b.md (b.md doesn't link back)
Missing metadata:  wiki/quick-note.md (no frontmatter)

Summary: 3 errors, 2 warnings
```

---

## Workflow: Compile (Advanced)

**Purpose:** Full rebuild of the wiki from all sources.

**Trigger:** User says "Compile" or "Rebuild wiki"

**Steps:**
1. List all files in `/raw`.
2. List all existing pages in `/wiki`.
3. Ingest each source document (create/update pages).
4. Run Lint to verify consistency.
5. Report the full state of the wiki.

---

## Page Template

When creating a new wiki page, use this structure:

```markdown
---
source: raw/source-filename.ext
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: [relevant, tags]
---

# Page Title

> One-line summary of what this page covers.

## Content

[Main content here]

## Related Pages

- [[related-page-1]] — Brief description of relationship
- [[related-page-2]] — Brief description of relationship

---

*Last compiled: YYYY-MM-DD*
```

---

## Guidelines for the LLM

- **Be precise.** Prefer specifics over vague summaries.
- **Preserve nuance.** If a source document contains caveats or trade-offs, capture them.
- **Track provenance.** Always note which source document a fact came from.
- **Compound over time.** Each Ingest should make the wiki richer, not just bigger.
  Look for connections between new and existing knowledge.
- **Never hallucinate.** If something isn't in the source documents or wiki, don't invent it.
  Say "not yet documented" instead.
