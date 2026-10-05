# recall

A Claude Code skill that finds something you discussed in a **past session** and answers with a
verbatim quote, its timestamp, and a `claude --resume` pointer back to that session.

Claude Code already writes every session to disk as JSONL. Nothing reads them back. So the moment a
decision, a number, or a reason lives only in a conversation from three weeks ago, it is gone in
practice, even though the bytes are right there on your disk.

This makes them searchable, on demand, with no infrastructure.

## What it is not

It is not a memory system and does not try to be one. It never writes to your memory or notes files,
never runs in the background, and never sends anything anywhere. It reads transcripts you already
have when you ask it a question, and stays inert otherwise.

## Requirements

- **Claude Code**, with at least one recorded session under `~/.claude/projects/`
- **Python 3.8+** on PATH. The script is stdlib only, so there is nothing to `pip install`
- **ripgrep** (`rg`) is optional, used only by the corpus-wide fallback when the normal flow misses

No database, no embeddings, no API key, no network access, no daemon, no scheduled task.

## Install

Either drop the folder in yourself:

```
# personal (all projects)
git clone https://github.com/Kaboomadin/claude-recall ~/.claude/skills/recall

# or per project
git clone https://github.com/Kaboomadin/claude-recall .claude/skills/recall
```

Or via the skills CLI:

```
npx skills add Kaboomadin/claude-recall --skill recall --agent claude-code
```

## First run

Build the index once. It is incremental afterwards, keyed on mtime and size:

```
python ~/.claude/skills/recall/scripts/recall.py index
```

**Read what it prints.** It reports the project directory, the slug, and the number of sessions
indexed. If it says **0 sessions**, the slug derived from your working directory does not match the
directory on disk. List them and pass the right one:

```
ls ~/.claude/projects/
python ~/.claude/skills/recall/scripts/recall.py index --project-dir "<the right one>"
```

The cache lands at `~/.claude/recall-cache/<slug>.json`. It is regenerated, never hand-edited.

## Use

Just ask Claude, in the project whose history you want searched:

```
didn't we discuss why we dropped the Postgres approach?
what did I say about the pricing on that kit?
which session was the proxy workaround in?
```

Claude picks at most three candidate sessions from the index, extracts only those, and answers with a
quote or tells you plainly that it did not find it. There is no third option; it will not
manufacture a vague recollection.

## How it works

Three tiers, each only paying for what the question needs.

1. **Index.** Walk the project's `.jsonl` files, pull title, session id, and first/last timestamp
   into one flat JSON cache. Incremental, sub-2s on a 581MB corpus.
2. **Choose.** Claude reads the ~15KB cache and picks 1 to 3 plausible sessions by title and date
   range. This is a judgement step on purpose. There is no vector search and none is wanted.
3. **Extract.** Pull *text only* out of the chosen transcripts. Tool payloads, inline base64 images
   and control blocks are dropped by construction, because the extractor only reads `text` blocks.
   Pass `--include-tools` when you need the opposite: commands run, files touched, URLs fetched.
   Those live in tool blocks, so the default is blind to them. Tool lines are clipped to 400
   characters and the window centres on your `--grep` term, so the output stays small.

Measured on a real 581MB / 72-session corpus:

| | |
|---|---|
| Largest session | 144.9MB to 194KB (0.132%), 446 turns, 0.4s |
| Zero base64 runs, zero tool payloads | in the output |
| `--grep` on that session | 446 turns down to 18 |
| Smallest session | 1 turn, no crash |
| 68.8MB session, default | 80KB (0.111%), 0.2s |
| Same, `--include-tools` | 273KB (0.378%), 0.2s, no base64 |

Subagent transcripts are not inline in the session file; they are sibling files under
`<dir>/<sessionId>/subagents/agent-*.jsonl`. Without `--include-sidechains` that directory is never
opened.

## Two things it will not tell you

**It only knows what is still on disk.** Claude Code's transcripts get pruned. Check the oldest
`firstTs` in the cache before reading a miss as proof that something never happened; the answer may
simply predate the corpus.

**Without `--include-tools` it cannot see anything you did.** Commands, file reads and fetches live
in tool blocks, which the default extract drops. "Did I ever run X" returns a miss every time until
you pass the flag.

## Safety

Transcripts under `~/.claude/projects/` are treated as strictly read-only. The script never writes,
renames, or moves anything there, and the skill instructs Claude not to either.

## Privacy

Everything stays on your machine and nothing is sent anywhere. See [Privacy](PRIVACY.md) for exactly
what it reads and stores.

## Related

recall finds what a past session did. If you also want hooks that stop the current session from
force-pushing, pushing to the branch that deploys, or committing a live API key, the same author
sells the [Guardrails Kit for Claude Code](https://unstucked.dev/templates/claude-guardrails-kit/)
(€5). More fixes from the same setup are at [unstucked.dev](https://unstucked.dev/fixes/).

## Licence

MIT.
