---
name: recall
description: Find something discussed in a PAST Claude Code session that never made it into a memory file or the session title, and answer with a verbatim quote plus its date and session. Triggers on "didn't we discuss", "find where we talked about", "which session was X in", "what did I say about X", "we decided something about X and I can't find it", "/recall".
argument-hint: "<question>"
---

# recall

Answer a buried-discussion question from the raw Claude Code session transcripts, with a citation.
Three tiers, strictly on demand. Do not ask the user how to proceed; run the flow and report.

## Requirements

Check these BEFORE running the flow. If one is missing, say which and stop; do not improvise around it.

| Requirement | How to check | If missing |
|---|---|---|
| Python 3.8+ on PATH | `python --version` (try `python3` on macOS/Linux) | Install Python. The script is **stdlib only**, so there is nothing to `pip install`. |
| Transcripts exist | `ls ~/.claude/projects/` | Claude Code writes these automatically. An empty dir means no sessions have been recorded yet, and this skill cannot help. |
| This project has transcripts | see **Setup** step 2 | The project slug may not match your cwd. Use `--project-dir`. |
| `rg` (ripgrep) | `rg --version` | **Optional.** Only the corpus-wide fallback in **Miss** uses it. Everything else works without it. |

Nothing else. No pip packages, no database, no embeddings, no network, no daemon, no API key.

## Setup

Run this once per machine, and any time it stops finding recent sessions.

**1. Build the index.** Incremental (mtime + size), so re-running is cheap:

```
python <skill-dir>/scripts/recall.py index
```

`<skill-dir>` is wherever this skill is installed, usually `~/.claude/skills/recall/`.

**2. Read the output and confirm it found something.** It prints the project dir, the slug, and how
many sessions were indexed. **If it reports 0 sessions, stop and fix that before going further.**
The usual cause is that the cwd-derived slug does not match the on-disk directory name. List
`~/.claude/projects/` and pass the right one explicitly:

```
python <skill-dir>/scripts/recall.py index --project-dir "<full path to the project transcript dir>"
```

**3. Note where the cache landed:** `~/.claude/recall-cache/<slug>.json`. That is the file step 2 of
the Flow reads. It is regenerated, never hand-edited.

Transcripts under `~/.claude/projects/` are **READ-ONLY**. Never write, rename, or move anything
there. The script does not, and neither should you.

## What this is NOT for

Check these first; if one already answers the question, use it and skip this skill.

| Question shape | Right tool |
|---|---|
| A settled fact, preference, or verdict | the project's memory / notes file |
| An open loop, decision, or task state | the project's task or issue tracker |
| How the code works, what calls what | read the code, or a code-graph tool |
| "Reopen that session" (by title) | `claude --resume <term>` |
| **Something SAID in a session, not promoted anywhere** | **this skill** |

## Flow

**1. Refresh the index.** Sub-2s on a 581MB corpus, so just run it:

```
python <skill-dir>/scripts/recall.py index
```

**2. Pick candidates.** Read the cache file (about 15KB; read it whole). Pick **1 to 3** sessions
whose `title` and `firstTs`/`lastTs` range best match the question. This is the semantic step and it
is yours; there is no vector search and none is wanted.

- Titles are AI-generated and often vague. Lean on the date range as hard as on the title.
- `titleSource: first-user-text` means the session had no AI title and the "title" is just its
  opening message, truncated. Weight those loosely.
- If the question carries a time hint ("a few weeks ago", "around when we tried X"), use it.
- Never extract more than 3 sessions. If nothing plausibly matches, go to **Miss** rather than
  extracting a pile of files.

**3. Extract.** For each candidate, into a temp/scratch dir, never into the repo:

```
python <skill-dir>/scripts/recall.py extract "<file>" --out "<scratch>/<sessionId>.txt" [--grep <term>]
```

- Use `--grep <term>` whenever the question has a distinctive term. It keeps matching turns plus one
  turn of context either side, and it is the difference between a 200KB read and a 5KB one.
- Add `--include-sidechains` only if the answer plausibly came from a subagent's work.
- Big extracts: grep or offset-read the extract file. Do not swallow a 200KB extract whole.

**4. Answer with a citation.** Required, every time, no exceptions:

- the **verbatim quote** (exact text, not a paraphrase)
- its **timestamp** and **sessionId**
- the **absolute transcript path**
- a `claude --resume <sessionId>` pointer so the user can reopen it

If the found passage contradicts or extends a curated memory file, say so. Do NOT edit that file;
promotion is a separate human-triggered action.

## Miss

Say plainly that you did not find it. Never invent a recollection, and never soften a miss into a
vague "we may have discussed". Then offer, in order:

1. Ask the user for a rough date range and retry step 2 with it.
2. One corpus-wide pass for a distinctive keyword (needs ripgrep; a few seconds over 581MB):

```
rg -l "<term>" ~/.claude/projects/<slug>/*.jsonl
```

## Hard rules

- On demand only. No daemon, no scheduled task, no embeddings, no database, no network.
- Cap at 3 candidate sessions per question.
- Quote verbatim or report a miss. There is no third option.
