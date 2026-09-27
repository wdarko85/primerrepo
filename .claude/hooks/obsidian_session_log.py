#!/usr/bin/env python3
"""Claude Code hook: escribe/actualiza una nota de Obsidian por cada sesión.

Se ejecuta en los eventos Stop (tras cada respuesta) y SessionEnd. Lee el JSON
del hook por stdin, recorre el transcript de la sesión y genera una nota en
"$OBSIDIAN_VAULT/$OBSIDIAN_FOLDER/<fecha> <proyecto> <id>.md". La nota se
sobrescribe en cada ejecución, así que siempre refleja el estado más reciente.

Variables de entorno:
  OBSIDIAN_VAULT         Ruta al vault (obligatoria; si falta, el hook no hace nada)
  OBSIDIAN_FOLDER        Carpeta dentro del vault (por defecto "Claude Sessions")
  OBSIDIAN_DAILY_FOLDER  Si se define, añade un enlace a la nota en la daily note
                         "<vault>/<carpeta>/YYYY-MM-DD.md"
  OBSIDIAN_GIT_PUSH      "1" para hacer commit + push si el vault es un repo git
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MAX_PROMPT_CHARS = 500
MAX_SUMMARY_CHARS = 2000


def run(cmd, cwd):
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def truncate(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def parse_transcript(path):
    info = {"title": None, "prompts": [], "files": [], "last_reply": "", "start": None}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return info
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        if info["start"] is None and entry.get("timestamp"):
            info["start"] = entry["timestamp"]
        if kind == "ai-title" and entry.get("aiTitle"):
            info["title"] = entry["aiTitle"]
        elif kind == "user" and not entry.get("isMeta") and not entry.get("isSidechain"):
            origin = (entry.get("origin") or {}).get("kind")
            if origin not in (None, "human"):
                continue
            content = entry.get("message", {}).get("content")
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                continue
            text = text_of(content).strip()
            if text and not text.startswith("<"):
                info["prompts"].append(text)
        elif kind == "assistant" and not entry.get("isSidechain"):
            content = entry.get("message", {}).get("content")
            text = text_of(content).strip()
            if text:
                info["last_reply"] = text
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") in ("Edit", "Write", "NotebookEdit")
                    ):
                        fp = (block.get("input") or {}).get("file_path") or (
                            block.get("input") or {}
                        ).get("notebook_path")
                        if fp and fp not in info["files"]:
                            info["files"].append(fp)
    return info


def safe_name(text):
    bad = '\\/:*?"<>|#^[]'
    return "".join(c for c in text if c not in bad).strip()


def build_note(data, info, cwd, event):
    project = Path(cwd).name
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
    commits = ""
    if info["start"]:
        commits = run(["git", "log", f"--since={info['start']}", "--format=- `%h` %s"], cwd)
    title = info["title"] or (info["prompts"][0].splitlines()[0][:80] if info["prompts"] else "Sesión")
    now = datetime.now().astimezone()

    files = []
    for fp in info["files"]:
        try:
            files.append(str(Path(fp).relative_to(cwd)))
        except ValueError:
            files.append(fp)

    out = [
        "---",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"updated: {now.isoformat(timespec='seconds')}",
        f"project: {json.dumps(project)}",
        f"branch: {json.dumps(branch)}",
        f"session_id: {data.get('session_id', '')}",
        f"status: {'finished' if event == 'SessionEnd' else 'active'}",
        "tags: [claude-session]",
        "---",
        "",
        f"# {title}",
        "",
        "## Peticiones",
    ]
    out += [f"- {truncate(p, MAX_PROMPT_CHARS).replace(chr(10), ' ')}" for p in info["prompts"]] or ["- (ninguna)"]
    out += ["", "## Archivos modificados"]
    out += [f"- `{f}`" for f in files] or ["- (ninguno)"]
    if commits:
        out += ["", "## Commits", commits]
    if info["last_reply"]:
        out += ["", "## Última respuesta de Claude", truncate(info["last_reply"], MAX_SUMMARY_CHARS)]
    return title, "\n".join(out) + "\n"


def link_in_daily(vault, note_path):
    folder = os.environ.get("OBSIDIAN_DAILY_FOLDER")
    if not folder:
        return
    daily = vault / folder / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    link = f"- [[{note_path.relative_to(vault).with_suffix('').as_posix()}]]"
    existing = daily.read_text(encoding="utf-8") if daily.exists() else ""
    if link in existing:
        return
    if "## Claude Code" not in existing:
        existing = existing.rstrip() + ("\n\n" if existing.strip() else "") + "## Claude Code\n"
    daily.write_text(existing.rstrip("\n") + "\n" + link + "\n", encoding="utf-8")


def git_push(vault, message):
    if os.environ.get("OBSIDIAN_GIT_PUSH") != "1" or not (vault / ".git").exists():
        return
    run(["git", "add", "-A"], vault)
    if not run(["git", "status", "--porcelain"], vault):
        return
    run(["git", "commit", "-m", message], vault)
    run(["git", "pull", "--rebase", "--autostash"], vault)
    run(["git", "push"], vault)


def main():
    vault_env = os.environ.get("OBSIDIAN_VAULT")
    if not vault_env:
        return
    data = json.load(sys.stdin)
    transcript = data.get("transcript_path")
    if not transcript:
        return
    vault = Path(vault_env).expanduser()
    cwd = data.get("cwd") or os.getcwd()
    event = data.get("hook_event_name", "Stop")

    info = parse_transcript(transcript)
    if not info["prompts"]:
        return
    title, note = build_note(data, info, cwd, event)

    folder = vault / os.environ.get("OBSIDIAN_FOLDER", "Claude Sessions")
    folder.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    session = str(data.get("session_id", ""))[:8]
    # Reutiliza la nota existente de esta sesión aunque cambie el título o el día
    matches = sorted(folder.glob(f"* {session}.md")) if session else []
    note_path = matches[0] if matches else folder / f"{date} {safe_name(Path(cwd).name)} {session}.md"
    note_path.write_text(note, encoding="utf-8")

    link_in_daily(vault, note_path)
    git_push(vault, f"Claude session: {title[:60]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # un hook nunca debe romper la sesión
        print(f"obsidian_session_log: {exc}", file=sys.stderr)
