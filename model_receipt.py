#!/usr/bin/env python3
"""model-receipt: see which model actually answered in your coding agent sessions.

Reads the session logs that Claude Code and Codex already keep on your computer,
prints a receipt, and writes model-receipt.json for the 3D viewer.

Read only. It never changes your settings, never sends anything anywhere,
and only looks at model names, reply counts, effort levels, token counts and
timestamps. It does not read your prompts, code or replies.

Usage:
    python3 model_receipt.py                 # scan, print a receipt, write model-receipt.json
    python3 model_receipt.py --days 7        # only sessions from the last 7 days
    python3 model_receipt.py --anonymize     # hide project folder names in the JSON
    python3 model_receipt.py view            # scan, then open the 3D viewer with your data

https://github.com/guoziyu415/model-receipt
"""

import argparse
import collections
import datetime as dt
import glob
import hashlib
import json
import os
import sys
import webbrowser

VERSION = "1.0.0"
VIEWER_URL = "https://code415.dev/demos/2026-09-27/model-receipt"
SKIP_MODELS = {"", "<synthetic>", None}


# ---------------------------------------------------------------- helpers

def parse_time(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_jsonl(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def text_of(content):
    """Return the text of a user message, only to spot slash commands."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def short_id(s):
    return hashlib.sha1(str(s).encode()).hexdigest()[:10]


def pretty(model):
    """claude-opus-4-8 -> Opus 4.8, gpt-5.6-sol stays as is."""
    m = str(model)
    if m.startswith("claude-"):
        parts = m[len("claude-"):].split("-")
        fam = parts[0].capitalize()
        ver = ".".join(p for p in parts[1:] if p.isdigit() and len(p) < 4)
        return (fam + " " + ver).strip()
    return m


# ---------------------------------------------------------------- Claude Code

SKIP_DIRS = {"node_modules", ".git", ".Trash", "__pycache__", ".venv", "venv", "site-packages",
             ".npm", ".nvm", ".cache", ".conda", ".anaconda", ".gem", ".pub-cache", ".dart_tool",
             ".dart-tool", ".swiftpm", ".terraform.d", "Pictures", "Music", "Movies", "Photos Library.photoslibrary",
             "subagents", "tool-results", "blob_storage", "IndexedDB", "Local Storage", "Session Storage",
             "Service Worker", "WebStorage", "Crashpad", "Logs", "logs", "vm_bundles"}
MAX_FILE = 400 * 1024 * 1024


def default_claude_roots():
    """Where Claude Code and the Claude desktop app usually keep session logs."""
    home = os.path.expanduser("~")
    roots = [os.path.join(home, ".claude", "projects")]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        roots.append(os.path.join(os.path.expanduser(env), "projects"))
    if sys.platform == "darwin":
        roots.append(os.path.join(home, "Library", "Application Support", "Claude"))
    elif os.name == "nt":
        if os.environ.get("APPDATA"):
            roots.append(os.path.join(os.environ["APPDATA"], "Claude"))
    else:
        roots.append(os.path.join(home, ".config", "Claude"))
    return roots


def walk_jsonl(root, deep=False):
    """Yield .jsonl files under root, skipping caches and subagent folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or "cache" in d.lower():
                continue
            if deep and dirpath == os.path.expanduser("~/Library") and d != "Application Support":
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if fn.endswith(".jsonl"):
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(p) <= MAX_FILE:
                        yield p
                except OSError:
                    pass


def looks_like_claude_log(path):
    """True if the first 200 lines contain a Claude reply with a model name."""
    for i, d in enumerate(read_jsonl(path)):
        if i > 200:
            return False
        msg = d.get("message")
        if d.get("type") == "assistant" and isinstance(msg, dict) and str(msg.get("model", "")).startswith("claude"):
            return True
        if d.get("type") in ("user", "summary", "system") and "sessionId" in d:
            return True
    return False


def scan_claude(roots, since, deep=False):
    """One entry per main session file. Subagent files are counted with their session."""
    sessions, seen, sources = [], set(), collections.Counter()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path in walk_jsonl(root, deep):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            try:
                if not root.endswith(os.path.join(".claude", "projects")) and not looks_like_claude_log(path):
                    continue
                s = scan_claude_session(path, since)
            except Exception:
                continue
            if s:
                s["source"] = tilde(os.path.dirname(os.path.dirname(path)) if os.path.basename(os.path.dirname(os.path.dirname(path))) == "projects" else os.path.dirname(path))
                sources[s["source"]] += 1
                sessions.append(s)
    sessions.sort(key=lambda s: s["started"] or "")
    return sessions, dict(sources)


def tilde(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p.startswith(home) else p


def scan_claude_session(path, since):
    seen = set()           # message ids already counted; one reply can span several log lines
    runs = []              # run length encoding of reply models, in order
    tokens = collections.Counter()
    efforts = collections.Counter()
    switches = []
    pending_model_cmd = False
    started = ended = None
    project = None
    n = 0
    for d in read_jsonl(path):
        t = d.get("type")
        ts = d.get("timestamp")
        if ts:
            started = started or ts
            ended = ts
        if project is None and d.get("cwd"):
            project = os.path.basename(str(d["cwd"]).rstrip("/")) or str(d["cwd"])
        if t == "user":
            um = d.get("message")
            txt = text_of(um.get("content") if isinstance(um, dict) else um)
            if "<command-name>/model</command-name>" in txt:
                pending_model_cmd = True
            continue
        if t != "assistant" or d.get("isSidechain"):
            continue
        msg = d.get("message")
        if not isinstance(msg, dict):
            continue
        model = msg.get("model")
        if not isinstance(model, str) or model in SKIP_MODELS:
            continue
        mid = msg.get("id") or d.get("requestId") or d.get("uuid")
        if mid in seen:
            continue
        seen.add(mid)
        usage = msg.get("usage")
        if isinstance(usage, dict):
            try:
                tokens[model] += int(usage.get("output_tokens") or 0)
            except (TypeError, ValueError):
                pass
        if d.get("effort"):
            efforts[str(d["effort"])] += 1
        if runs and runs[-1][0] == model:
            runs[-1][1] += 1
        else:
            if runs:
                switches.append({"at": n, "from": runs[-1][0], "to": model,
                                 "by": "you" if pending_model_cmd else "auto"})
                pending_model_cmd = False
            runs.append([model, 1])
        n += 1
    if not n:
        return None
    t0 = parse_time(started)
    if since and t0 and t0 < since:
        return None
    # subagents live next to the session file
    sub = collections.Counter()
    subdir = os.path.join(path[:-len(".jsonl")], "subagents")
    for sp in glob.glob(os.path.join(subdir, "*.jsonl")):
        sseen = set()
        for d in read_jsonl(sp):
            if d.get("type") != "assistant":
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            model = msg.get("model")
            mid = msg.get("id") or d.get("uuid")
            if not isinstance(model, str) or model in SKIP_MODELS or mid in sseen:
                continue
            sseen.add(mid)
            sub[model] += 1
    t1 = parse_time(ended)
    return {
        "id": short_id(path),
        "project": project or "unknown",
        "started": started,
        "minutes": round((t1 - t0).total_seconds() / 60, 1) if t0 and t1 else None,
        "effort": efforts.most_common(1)[0][0] if efforts else None,
        "replies": runs,
        "switches": switches,
        "output_tokens": dict(tokens),
        "subagent_replies": dict(sub),
    }


# ---------------------------------------------------------------- Codex

def scan_codex(root, since):
    """Codex logs the model you picked for each turn, not the model that answered."""
    turns = collections.Counter()
    sessions = 0
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        got = False
        for d in read_jsonl(path):
            if d.get("type") != "turn_context":
                continue
            t0 = parse_time(d.get("timestamp"))
            if since and t0 and t0 < since:
                continue
            pl = d.get("payload")
            model = pl.get("model") if isinstance(pl, dict) else None
            if isinstance(model, str) and model:
                turns[model] += 1
                got = True
        sessions += got
    return {"sessions": sessions, "turns_by_picked_model": dict(turns)}


# ---------------------------------------------------------------- receipt

def totals(sessions):
    by_model = collections.Counter()
    handed = auto = you = 0
    for s in sessions:
        for m, c in s["replies"]:
            by_model[m] += c
        if s["switches"]:
            handed += 1
            if any(w["by"] == "auto" for w in s["switches"]):
                auto += 1
            else:
                you += 1
    return by_model, handed, auto, you


def line(left, right="", width=40):
    left = str(left)
    right = str(right)
    space = max(1, width - len(left) - len(right))
    return "  " + left + " " * space + right


def print_receipt(data, days):
    W = 40
    rule = "  " + "-" * W
    out = []
    out.append("")
    out.append("  " + "MODEL RECEIPT".center(W))
    scope = "last %d days" % days if days else "all sessions on this computer"
    out.append("  " + scope.center(W))
    out.append(rule)
    cl = data.get("claude")
    if cl is None:
        out.append("  No Claude session logs found.")
        out.append("  Try --find-all, or --claude-dir FOLDER.")
        out.append(rule)
    else:
        sessions = cl["sessions"]
        by_model, handed, auto, you = totals(sessions)
        total = sum(by_model.values())
        out.append(line("CLAUDE CODE", "", W))
        out.append(line("Sessions", "{:,}".format(len(sessions)), W))
        out.append(line("Replies", "{:,}".format(total), W))
        out.append(rule)
        for m, c in by_model.most_common():
            pct = "{:.1f}%".format(100.0 * c / total) if total else ""
            out.append(line(pretty(m)[:20], "{:>8,}  {:>6}".format(c, pct), W))
        out.append(rule)
        if cl.get("sources"):
            out.append("  Found in:")
            for src, c in sorted(cl["sources"].items(), key=lambda x: -x[1]):
                out.append(line("  " + (src if len(src) <= 30 else "..." + src[-27:]), str(c), W))
            out.append(rule)
        out.append(line("Sessions that changed model", str(handed), W))
        out.append(line("  with no /model from you", str(auto), W))
        out.append(line("  after your /model", str(you), W))
        if not sessions:
            out.append("  No Claude Code sessions found.")
        out.append(rule)
    cx = data.get("codex")
    if cx and cx["turns_by_picked_model"]:
        turns = cx["turns_by_picked_model"]
        tot = sum(turns.values())
        out.append(line("CODEX", "", W))
        out.append(line("Turns, by the model you picked", "{:,}".format(tot), W))
        for m, c in sorted(turns.items(), key=lambda x: -x[1]):
            out.append(line(m[:20], "{:>8,}  {:>6}".format(c, "{:.1f}%".format(100.0 * c / tot)), W))
        out.append("  Codex logs the model you picked, not")
        out.append("  the one that answered, so a reroute")
        out.append("  by OpenAI would not show up here.")
        out.append(rule)
    out.append("  " + "THANK YOU FOR COMPUTING".center(W))
    out.append("")
    print("\n".join(l.rstrip() for l in out))


# ---------------------------------------------------------------- main

def build(args):
    since = None
    if args.days:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    data = {"tool": "model-receipt", "version": VERSION,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "days": args.days}
    roots = default_claude_roots() + [os.path.expanduser(d) for d in (args.claude_dir or [])]
    if args.find_all:
        roots.append(os.path.expanduser("~"))
    if any(os.path.isdir(r) for r in roots):
        sessions, sources = scan_claude(roots, since, deep=args.find_all)
        if args.anonymize:
            names = {}
            for s in sessions:
                names.setdefault(s["project"], "project %d" % (len(names) + 1))
                s["project"] = names[s["project"]]
                s.pop("source", None)
            sources = {}
        data["claude"] = {"sessions": sessions, "sources": sources}
    else:
        data["claude"] = None
    codex_root = os.path.expanduser(args.codex_dir)
    if not args.no_codex and os.path.isdir(codex_root):
        data["codex"] = scan_codex(codex_root, since)
    return data


def write_viewer(data, out_html):
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = os.path.join(here, "docs", "index.html")
    if not os.path.exists(tpl):
        return False
    with open(tpl, encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(data).replace("</", "<\\/")
    tag = '<script id="receipt-data" type="application/json">' + blob + "</script>"
    html = html.replace("<!--RECEIPT_DATA-->", tag, 1)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="model_receipt.py", description="See which model actually answered in your Claude Code and Codex sessions.")
    p.add_argument("command", nargs="?", default="scan", choices=["scan", "view"], help="scan (default) or view")
    p.add_argument("--days", type=int, default=0, help="only sessions that started in the last N days")
    p.add_argument("--anonymize", action="store_true", help="replace project folder names with 'project 1', 'project 2' and so on")
    p.add_argument("--no-codex", action="store_true", help="skip Codex logs")
    p.add_argument("-o", "--out", default="model-receipt.json", help="where to write the JSON (default: model-receipt.json)")
    p.add_argument("--claude-dir", action="append", metavar="DIR", help="also scan this folder for Claude session logs; repeat for more folders")
    p.add_argument("--find-all", action="store_true", help="search your whole home folder for Claude session logs; slower")
    p.add_argument("--codex-dir", default="~/.codex/sessions", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version="model-receipt " + VERSION)
    args = p.parse_args(argv)

    data = build(args)
    print_receipt(data, args.days)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print("  Saved %s" % os.path.abspath(args.out))

    if args.command == "view":
        out_html = os.path.splitext(args.out)[0] + ".html"
        if write_viewer(data, out_html):
            print("  Opening %s" % os.path.abspath(out_html))
            webbrowser.open("file://" + os.path.abspath(out_html))
        else:
            print("  Open %s and drop %s on the page." % (VIEWER_URL, args.out))
            webbrowser.open(VIEWER_URL)
    else:
        print("  See it in 3D: open %s and drop the file on the page," % VIEWER_URL)
        print("  or run: python3 model_receipt.py view")
    return 0


if __name__ == "__main__":
    sys.exit(main())
