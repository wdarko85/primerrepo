#!/usr/bin/env python3
"""Claude Code hook: escribe/actualiza una nota de Obsidian por cada sesión.

Se ejecuta en los eventos Stop (tras cada respuesta) y SessionEnd. Lee el JSON
del hook por stdin, recorre el transcript de la sesión y genera una nota en
"$OBSIDIAN_VAULT/$OBSIDIAN_FOLDER/<proyecto>/<fecha> <título de la sesión>.md". La nota
se sobrescribe en cada ejecución, así que siempre refleja el estado más reciente.

Importar sesiones antiguas (todas las de ~/.claude/projects, de todos los
proyectos):  python3 obsidian_session_log.py --backfill

Variables de entorno:
  OBSIDIAN_VAULT         Ruta al vault (obligatoria; si falta, el hook no hace nada)
  OBSIDIAN_FOLDER        Carpeta dentro del vault (por defecto "Claude Sessions")
  OBSIDIAN_DAILY_FOLDER  Si se define, añade un enlace a la nota en la daily note
                         "<vault>/<carpeta>/YYYY-MM-DD.md"
  OBSIDIAN_GIT_PUSH      "1" para hacer commit + push si el vault es un repo git
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MAX_PROMPT_CHARS = 500
MAX_SUMMARY_CHARS = 2000

CODE_START_RE = re.compile(r'^(//|/\*|#!|\$ |> |@"|@\')')
SHELL_CMD_RE = re.compile(r"^(docker|npm|git|curl|sudo|python3?|bash|sh|brew|pip3?|node|yarn)\b")


def looks_like_code_or_path(text):
    """Detecta la primera línea de un script, ruta o comando pegado, para no
    usarla como título de la sesión (p. ej. "// ==UserScript==" o
    "/Applications/OF-DL.app" en vez de lo que el usuario pidió de verdad)."""
    t = text.strip()
    if not t:
        return True
    head = t[:60]
    if CODE_START_RE.match(t) or "==UserScript==" in t or "==UserStyle==" in t:
        return True
    first_word = t.split(None, 1)[0]
    if first_word.startswith("/") and first_word.count("/") >= 2:
        return True
    if SHELL_CMD_RE.match(t):
        return True
    if t.startswith("at ") and "(" in t:  # línea de stack trace
        return True
    letters = sum(c.isalpha() for c in head)
    if len(head) >= 15 and letters / len(head) < 0.35:  # línea de símbolos/código
        return True
    return False


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
    info = {"title": None, "prompts": [], "files": [], "last_reply": "", "start": None, "end": None, "cwd": None}
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
        if entry.get("timestamp"):
            info["start"] = info["start"] or entry["timestamp"]
            info["end"] = entry["timestamp"]
        if info["cwd"] is None and entry.get("cwd"):
            info["cwd"] = entry["cwd"]
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


GENERIC_DIRS = {"", "Documents", "Documentos", "Desktop", "Escritorio", "Downloads", "Descargas", "tmp"}


def safe_name(text, limit=90):
    bad = '\\/:*?"<>|#^[]'
    name = " ".join("".join(c for c in text if c not in bad).split())
    return name[:limit].strip(" .")


def project_name(cwd, files):
    """Nombre legible del proyecto: el repo git, o la carpeta donde se trabajó de verdad."""
    candidates = [cwd] + [str(Path(f).parent) for f in files]
    for path in candidates:
        if Path(path).is_dir():
            top = run(["git", "rev-parse", "--show-toplevel"], path)
            if top:
                return Path(top).name
    name = Path(cwd).name
    if name not in GENERIC_DIRS and Path(cwd) != Path.home():
        return name
    # Sesión abierta desde una carpeta genérica: usa la carpeta de los archivos tocados
    for f in files:
        parts = Path(f).parts
        if len(parts) > 3 and parts[1] == "Volumes":
            return parts[3]  # /Volumes/<disco>/<proyecto>/...
        try:
            rel = Path(f).relative_to(cwd).parts
        except ValueError:
            continue
        if len(rel) > 1:
            return rel[0]
    return "General"


def parse_time(stamp):
    # fromisoformat no acepta "Z" en Python < 3.11 (el de macOS suele ser 3.9)
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return datetime.now().astimezone()


def pick_title(info):
    """Título de la sesión: el que puso Claude si es legible; si no, la primera
    petición del usuario que no sea código/ruta/comando pegado; si ninguna lo
    es, "Sesión"."""
    if info["title"] and not looks_like_code_or_path(info["title"]):
        return info["title"]
    for p in info["prompts"]:
        line = p.splitlines()[0].strip()
        if line and not looks_like_code_or_path(line):
            return line[:80]
    return "Sesión"


def build_note(session_id, info, cwd, event):
    project = project_name(cwd, info["files"])
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
    commits = ""
    if info["start"]:
        log = ["git", "log", f"--since={info['start']}", "--format=- `%h` %s"]
        if event == "SessionEnd" and info["end"]:
            log.insert(3, f"--until={info['end']}")
        commits = run(log, cwd)
    title = pick_title(info)
    started = parse_time(info["start"])
    now = datetime.now().astimezone()

    files = []
    for fp in info["files"]:
        try:
            files.append(str(Path(fp).relative_to(cwd)))
        except ValueError:
            files.append(fp)

    out = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"date: {started.strftime('%Y-%m-%d')}",
        f"updated: {now.isoformat(timespec='seconds')}",
        f"project: {json.dumps(project, ensure_ascii=False)}",
        f"branch: {json.dumps(branch)}",
        f"session_id: {session_id}",
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
    return title, project, started, "\n".join(out) + "\n"


def link_in_daily(vault, note_path, day, title, session_id, old_path=None):
    folder = os.environ.get("OBSIDIAN_DAILY_FOLDER")
    if not folder:
        return
    daily = vault / folder / f"{day.strftime('%Y-%m-%d')}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    target = note_path.relative_to(vault).with_suffix("").as_posix()
    link = f"- [[{target}|{title}]]"
    existing = daily.read_text(encoding="utf-8") if daily.exists() else ""
    if link in existing:
        return
    # Si la nota cambió de nombre, sustituye el enlace viejo en vez de añadir otro
    olds = {f"{session_id[:8]}]]"}
    if old_path is not None:
        olds.add(f"[[{old_path.relative_to(vault).with_suffix('').as_posix()}")
    kept = [l for l in existing.splitlines() if not any(o in l for o in olds)]
    if len(kept) != len(existing.splitlines()):
        existing = "\n".join(kept) + "\n"
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


def find_note(root, session_id):
    """Busca la nota de una sesión por su session_id (o por el nombre antiguo con el id)."""
    marker = f"session_id: {session_id}\n"
    legacy = f" {str(session_id)[:8]}.md"
    for path in root.rglob("*.md"):
        if path.name.endswith(legacy):
            return path
        try:
            with open(path, encoding="utf-8") as fh:
                if marker in fh.read(1500):
                    return path
        except OSError:
            continue
    return None


def write_session(vault, session_id, transcript, cwd, event):
    info = parse_transcript(transcript)
    cwd = cwd or info["cwd"]
    if not info["prompts"] or not cwd or not session_id:
        return None
    title, project, started, note = build_note(session_id, info, cwd, event)

    root = vault / os.environ.get("OBSIDIAN_FOLDER", "Claude Sessions")
    folder = root / (safe_name(project) or "General")
    name = f"{started.strftime('%Y-%m-%d')} {safe_name(title) or 'Sesión'}"
    old_path = find_note(root, session_id)
    note_path = folder / f"{name}.md"
    if note_path.exists() and note_path != old_path:
        note_path = folder / f"{name} ({str(session_id)[:8]}).md"  # mismo título el mismo día

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(note, encoding="utf-8")
    if old_path is not None and old_path != note_path:
        old_path.unlink(missing_ok=True)
        try:
            old_path.parent.rmdir()  # borra la carpeta vieja si se quedó vacía
        except OSError:
            pass

    link_in_daily(vault, note_path, started, title, str(session_id), old_path)
    return title


def backfill(vault):
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
    count = 0
    for transcript in sorted(projects.glob("*/*.jsonl")):
        if write_session(vault, transcript.stem, transcript, None, "SessionEnd"):
            count += 1
    print(f"{count} sesiones importadas en {vault}")
    git_push(vault, f"Claude sessions: backfill de {count} sesiones")


def main():
    vault_env = os.environ.get("OBSIDIAN_VAULT")
    if not vault_env:
        if "--backfill" in sys.argv:
            print("Define OBSIDIAN_VAULT con la ruta de tu vault", file=sys.stderr)
        return
    vault = Path(vault_env).expanduser()
    if "--backfill" in sys.argv:
        backfill(vault)
        return
    data = json.load(sys.stdin)
    transcript = data.get("transcript_path")
    if not transcript:
        return
    title = write_session(
        vault,
        data.get("session_id", ""),
        transcript,
        data.get("cwd") or os.getcwd(),
        data.get("hook_event_name", "Stop"),
    )
    if title:
        git_push(vault, f"Claude session: {title[:60]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # un hook nunca debe romper la sesión
        print(f"obsidian_session_log: {exc}", file=sys.stderr)
