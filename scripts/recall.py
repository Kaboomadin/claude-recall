#!/usr/bin/env python3
"""
recall.py - helper script for the /recall skill.

Subcommands:
    index    Build/refresh the Tier-1 session metadata cache (this file).
    extract  Tier-3 text-only extraction from one session transcript.

Python 3 stdlib only, no third-party packages. Cross-platform: all paths are
resolved from Path.home(), so Windows, macOS and Linux all work.
Transcripts under ~/.claude/projects are read-only: this script never writes,
renames, or touches them.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

SCHEMA_VERSION = 1
HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
CACHE_DIR = HOME / ".claude" / "recall-cache"

# Command-wrapper / control-string noise that must not become a title, and
# must never be extracted as if it were conversation.
#
# `<task-notification>` was NOT in the 2026-07-13 design's list and was found
# on 2026-08-08 to be the second most common user-record shape in the corpus
# (30 of the last 25 sessions' user records). Background-task completions ride
# in user-role records exactly like tool_result does, so they must be stripped
# the same way. Re-check this list if extracts ever look padded again.
NOISE_PREFIXES = (
    "<command-name>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<task-notification>",
    "<system-reminder>",
)


def derive_slug(cwd_path: str) -> str:
    """Translate an absolute cwd path into the Claude Code project-dir slug.

    Rule (Task 4 finding): replace every non-alphanumeric character with
    '-', then match case-insensitively against real directory names under
    ~/.claude/projects to recover the actual on-disk casing. Falls back to
    the derived string if no match exists yet.
    """
    derived = re.sub(r"[^a-zA-Z0-9]", "-", cwd_path)
    if PROJECTS_DIR.is_dir():
        for entry in PROJECTS_DIR.iterdir():
            if entry.is_dir() and entry.name.lower() == derived.lower():
                return entry.name
    return derived


def resolve_project_dir(project_dir_arg):
    """Return (project_dir: Path, slug: str).

    --project-dir, when given, is treated as already resolved: cwd-based
    slug derivation is skipped entirely, and the slug is just its basename.
    """
    if project_dir_arg:
        p = Path(project_dir_arg)
        return p, p.name
    slug = derive_slug(str(Path.cwd()))
    return PROJECTS_DIR / slug, slug


def is_noise_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith(NOISE_PREFIXES):
        return True
    # Subagent-completion control string (design doc section 3).
    if stripped.startswith("agentId: ") and "SendMessage" in stripped:
        return True
    return False


# Per-block cap for tool text under --include-tools. Small on purpose: the point
# is to answer "was this command run", which the first line answers, not to
# reproduce the output. Without a cap one `cat` of a large file undoes the whole
# size guarantee that makes this tool usable.
MAX_TOOL_CHARS = 400

# A long unbroken run of base64-ish characters. Tool blocks carry embedded images
# and blobs; those are exactly what must never reach the output.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{200,}")


def _clip(text, limit=MAX_TOOL_CHARS, focus=None):
    """Collapse to one line, drop base64 runs, and keep a window of `limit`.

    The window is CENTRED ON `focus` when that term appears. Clipping from the
    start looks right and is useless: on the first attempt at this, the match
    that justified the whole feature sat ~2KB into a file-read result, so a
    head-truncation deleted the evidence and the search still returned nothing.
    Truncate around what was asked for, not around the beginning.
    """
    if not isinstance(text, str):
        return None
    text = _BASE64_RUN.sub("<blob>", text)
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    start = 0
    if focus:
        hit = text.lower().find(focus.lower())
        if hit >= 0:
            start = max(0, hit - limit // 3)
    window = text[start:start + limit]
    return ("..." if start else "") + window + ("..." if start + limit < len(text) else "")


def _tool_use_line(block, focus=None):
    """One line naming the tool and the part of its input worth searching.

    `command` is pulled out by name because "did I ever run X" is the question
    this exists for, and for Bash the command IS the content. Other tools fall
    back to their whole input, clipped.
    """
    name = block.get("name") or "tool"
    inp = block.get("input")
    detail = None
    if isinstance(inp, dict):
        for key in ("command", "file_path", "path", "pattern", "query", "url"):
            if isinstance(inp.get(key), str):
                detail = inp[key]
                break
        if detail is None:
            detail = json.dumps(inp, ensure_ascii=False)
    elif inp is not None:
        detail = str(inp)
    detail = _clip(detail, focus=focus)
    return f"[tool: {name}] {detail}" if detail else f"[tool: {name}]"


def extract_text(content, include_tools=False, focus=None):
    """Return plain text from a message.content value: plain string or a
    list of typed blocks.

    By default ONLY 'text' blocks contribute. That is what keeps inline base64
    images and tool payloads out, and it is why a 144.9MB session extracts to
    194KB.

    With include_tools=True, 'tool_use' and 'tool_result' blocks are summarised
    into one clipped line each. This exists because the default is blind to a
    whole class of question: a command you RAN lives in a tool block, not in
    conversation text, so "did I ever run X" could not be answered at all.
    Clipped and base64-stripped so the size guarantee still roughly holds.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif include_tools and btype == "tool_use":
                parts.append(_tool_use_line(block, focus=focus))
            elif include_tools and btype == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    inner = "\n".join(
                        b.get("text", "") for b in inner
                        if isinstance(b, dict) and b.get("type") == "text")
                line = _clip(inner if isinstance(inner, str) else None, focus=focus)
                if line:
                    parts.append(f"[tool result] {line}")
        return "\n".join(parts) if parts else None
    return None


def scan_session_file(path: Path):
    """Stream one transcript file once. Returns (title, titleSource,
    firstTs, lastTs), or None if the file could not be read at all.

    Tolerant of malformed/truncated trailing lines (a session file can be
    actively appended to while read) - unparsable lines are skipped, never
    fatal.
    """
    ai_title = None
    first_user_text = None
    first_ts = None
    last_ts = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue

                ts = obj.get("timestamp")
                if isinstance(ts, str) and ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                rtype = obj.get("type")

                if ai_title is None and rtype == "ai-title":
                    t = obj.get("aiTitle")
                    if isinstance(t, str) and t:
                        ai_title = t

                if first_user_text is None and rtype == "user" and not obj.get("isMeta"):
                    message = obj.get("message")
                    if isinstance(message, dict):
                        text = extract_text(message.get("content"))
                        if text and not is_noise_text(text):
                            first_user_text = text.strip()
    except OSError:
        return None

    if ai_title:
        return ai_title, "ai-title", first_ts, last_ts
    if first_user_text:
        return first_user_text[:120], "first-user-text", first_ts, last_ts
    # No usable title material anywhere in the file (e.g. an immediately
    # cleared session). Still tag with the closest schema value.
    return "(untitled session)", "first-user-text", first_ts, last_ts


def load_cache(cache_path: Path):
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {}
    return {entry["sessionId"]: entry for entry in data.get("sessions", []) if "sessionId" in entry}


def cmd_index(args):
    project_dir, slug = resolve_project_dir(args.project_dir)
    if not project_dir.is_dir():
        print(f"Project dir not found: {project_dir}", file=sys.stderr)
        return 1

    cache_path = CACHE_DIR / f"{slug}.json"
    cached_by_id = load_cache(cache_path)

    sessions = []
    scanned = 0
    reused = 0

    for path in sorted(project_dir.glob("*.jsonl")):
        session_id = path.stem
        try:
            st = path.stat()
        except OSError:
            continue
        mtime = st.st_mtime
        size = st.st_size

        cached = cached_by_id.get(session_id)
        if cached and cached.get("mtime") == mtime and cached.get("size") == size:
            sessions.append(cached)
            reused += 1
            continue

        result = scan_session_file(path)
        scanned += 1
        if result is None:
            continue
        title, title_source, first_ts, last_ts = result
        sessions.append({
            "sessionId": session_id,
            "title": title,
            "titleSource": title_source,
            "firstTs": first_ts,
            "lastTs": last_ts,
            "file": str(path),
            "mtime": mtime,
            "size": size,
        })
        # Deleted files are dropped automatically: sessions is only ever
        # populated from files currently on disk in this loop.

    sessions.sort(key=lambda e: e.get("firstTs") or "")

    cache_obj = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projectDir": str(project_dir),
        "sessions": sessions,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache_obj, f, indent=2)
    os.replace(tmp_path, cache_path)

    print(f"Project dir: {project_dir}")
    print(f"Slug:        {slug}")
    print(f"Indexed {len(sessions)} sessions ({scanned} scanned, {reused} reused from cache) -> {cache_path}")
    if not sessions:
        # Self-diagnosing: 0 sessions almost always means the cwd-derived slug does
        # not match the directory on disk, and the fix needs the two facts above.
        print(
            "\nNo sessions found. The slug above is derived from your working directory,"
            "\nand may not match the transcript directory Claude Code actually wrote."
            f"\nList the real ones and pass the right one explicitly:"
            f"\n  ls {PROJECTS_DIR}"
            f"\n  python recall.py index --project-dir \"<the right one>\""
        )
    return 0


def collect_turns(path: Path, agent_id=None, skip_sidechain=True, include_tools=False, focus=None):
    """Stream one transcript file and return a list of kept turns.

    A turn is {role, ts, uuid, agentId, text}. Kept: user/assistant records
    carrying plain-string content or 'text' blocks. Dropped: every other
    record type, isMeta records, isSidechain records when skip_sidechain
    (defensive - subagent turns live in sibling files, not inline), and
    turns whose text is nothing but command-wrapper noise.

    thinking/tool_use/tool_result/image blocks never contribute, because
    extract_text() only reads 'text' blocks. That is what keeps base64
    images and tool payloads out of the output.
    """
    turns = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") not in ("user", "assistant"):
                    continue
                if obj.get("isMeta"):
                    continue
                if skip_sidechain and obj.get("isSidechain"):
                    continue

                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                text = extract_text(message.get("content"), include_tools=include_tools, focus=focus)
                if not text or is_noise_text(text):
                    continue

                turns.append({
                    "role": obj.get("type"),
                    "ts": obj.get("timestamp") or "",
                    "uuid": obj.get("uuid") or "",
                    "agentId": agent_id,
                    "text": text.strip(),
                })
    except OSError:
        return []
    return turns


def apply_grep(turns, term):
    """Keep turns matching term case-insensitively, plus 1 turn of context
    either side. Order is preserved and no turn is emitted twice."""
    needle = term.lower()
    keep = set()
    for i, turn in enumerate(turns):
        if needle in turn["text"].lower():
            keep.update((i - 1, i, i + 1))
    return [t for i, t in enumerate(turns) if i in keep]


def cmd_extract(args):
    path = Path(args.session_file)
    if not path.is_file():
        print(f"Session file not found: {path}", file=sys.stderr)
        return 1

    turns = collect_turns(path, agent_id=None, skip_sidechain=True,
                          include_tools=args.include_tools, focus=args.grep)
    sidechain_count = 0

    if args.include_sidechains:
        # Correction 2026-07-13: subagent transcripts are NOT inline. They
        # are sibling files at <dir>/<sessionId>/subagents/agent-*.jsonl.
        subagents_dir = path.parent / path.stem / "subagents"
        if subagents_dir.is_dir():
            for agent_path in sorted(subagents_dir.glob("agent-*.jsonl")):
                agent_turns = collect_turns(agent_path, agent_id=agent_path.stem, skip_sidechain=False,
                                            include_tools=args.include_tools, focus=args.grep)
                sidechain_count += len(agent_turns)
                turns.extend(agent_turns)

    turns.sort(key=lambda t: t["ts"])

    if args.grep:
        turns = apply_grep(turns, args.grep)

    lines = []
    for turn in turns:
        header = f"[{turn['role']} {turn['ts']} {turn['uuid']}"
        if turn["agentId"]:
            header += f" {turn['agentId']}"
        lines.append(header + "]")
        lines.append(turn["text"])
        lines.append("")
    body = "\n".join(lines)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"Extracted {len(turns)} turns ({sidechain_count} from sidechains) -> {out_path}")
    else:
        sys.stdout.write(body)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="recall.py", description="Session-recall helper script.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build/refresh the Tier-1 session metadata cache.")
    index_parser.add_argument("--project-dir", default=None, help="Explicit project transcript directory (skips cwd-based slug derivation).")
    index_parser.set_defaults(func=cmd_index)

    extract_parser = subparsers.add_parser("extract", help="Write a text-only transcript for one session file.")
    extract_parser.add_argument("session_file", help="Path to one session .jsonl transcript.")
    extract_parser.add_argument("--out", default=None, help="Output file (default: stdout).")
    extract_parser.add_argument("--grep", default=None, help="Keep only turns matching this term (case-insensitive), plus 1 turn of context either side.")
    extract_parser.add_argument("--include-sidechains", action="store_true", help="Also merge subagent transcripts from <sessionId>/subagents/agent-*.jsonl.")
    extract_parser.add_argument("--include-tools", action="store_true", help="Also include tool calls and results, one clipped line each. Needed to answer 'did I ever run X', which lives in a tool block, not conversation text.")
    extract_parser.set_defaults(func=cmd_extract)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
