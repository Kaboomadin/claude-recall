# Privacy

recall runs entirely on your own machine. The author collects nothing, and no data is sent anywhere.

## What it reads

The Claude Code session transcripts already on your disk, under `~/.claude/projects/`. These can contain
anything you or Claude wrote in a session, including names, email addresses and other personal data.
The script only reads them. It never writes, renames or moves anything in that folder.

## What it stores

- An index file per project at `~/.claude/recall-cache/<slug>.json`, holding each session's id, title,
  first and last timestamps, and the transcript's path, size and modified time. A session without a title is listed by a short excerpt of its
  opening message.
- Plain-text extracts of up to three sessions per question, written to the folder you or Claude name with
  `--out`.

Both stay on your machine until you delete them. Deleting `~/.claude/recall-cache/` removes the index, and
the next run rebuilds it.

## What it sends

Nothing. The script uses only the Python standard library and makes no network connections. What Claude
does with an answer inside your session is governed by your Claude Code settings and Anthropic's own
privacy policy, not by this plugin.

## Contact

Open an issue at https://github.com/Kaboomadin/claude-recall/issues.
