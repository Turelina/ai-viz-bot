"""
Генератор отчёта по сессии.

Использование:
    python scripts/generate_session_report.py           # последние 5 коммитов
    python scripts/generate_session_report.py --n 10    # последние 10 коммитов
    python scripts/generate_session_report.py --since "2026-02-22"  # с даты
    python scripts/generate_session_report.py --save    # сохранить в logs/

Выводит: список коммитов с датами, изменёнными файлами и статистикой.
Помогает отследить, что и когда менялось в проекте.
"""

import subprocess
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Windows: переключить stdout на UTF-8
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def get_commits(n: int = 5, since: str | None = None) -> list[dict]:
    fmt = "%H|||%ai|||%an|||%s"
    cmd = ["log", f"--pretty=format:{fmt}"]
    if since:
        cmd += [f"--since={since}"]
    else:
        cmd += [f"-{n}"]

    raw = run_git(cmd)
    if not raw:
        return []

    commits = []
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) == 4:
            commits.append({
                "hash": parts[0][:8],
                "date": parts[1],
                "author": parts[2],
                "message": parts[3],
            })
    return commits


def get_commit_files(commit_hash: str) -> list[dict]:
    raw = run_git(["show", "--stat", "--pretty=format:", commit_hash])
    lines = [l for l in raw.splitlines() if l.strip() and "|" in l]
    files = []
    for line in lines:
        parts = line.strip().split("|")
        if len(parts) == 2:
            filepath = parts[0].strip()
            changes = parts[1].strip()
            files.append({"path": filepath, "changes": changes})
    return files


def get_diff_summary(commit_hash: str) -> tuple[int, int]:
    """Возвращает (insertions, deletions)."""
    raw = run_git(["show", "--shortstat", "--pretty=format:", commit_hash])
    insertions = deletions = 0
    for part in raw.split(","):
        part = part.strip()
        if "insertion" in part:
            insertions = int(part.split()[0])
        elif "deletion" in part:
            deletions = int(part.split()[0])
    return insertions, deletions


def get_current_branch() -> str:
    return run_git(["branch", "--show-current"])


def format_report(commits: list[dict], show_files: bool = True) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    branch = get_current_branch()

    lines = [
        "=" * 60,
        f"  ОТЧЁТ ПО ПРОЕКТУ",
        f"  Сгенерирован: {now}",
        f"  Ветка: {branch}",
        f"  Коммитов в отчёте: {len(commits)}",
        "=" * 60,
        "",
    ]

    total_insertions = 0
    total_deletions = 0

    for i, commit in enumerate(commits, 1):
        insertions, deletions = get_diff_summary(commit["hash"])
        total_insertions += insertions
        total_deletions += deletions

        lines += [
            f"[{i}] {commit['date'][:16]}  {commit['hash']}",
            f"    {commit['message']}",
            f"    +{insertions} строк добавлено, -{deletions} удалено",
        ]

        if show_files:
            files = get_commit_files(commit["hash"])
            for f in files:
                lines.append(f"    • {f['path']:50s} {f['changes']}")

        lines.append("")

    lines += [
        "-" * 60,
        f"  ИТОГО: +{total_insertions} добавлено, -{total_deletions} удалено",
        "-" * 60,
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Генератор отчёта по сессии")
    parser.add_argument("--n", type=int, default=5, help="Количество последних коммитов (по умолчанию 5)")
    parser.add_argument("--since", type=str, default=None, help="Показать коммиты с даты (напр. 2026-02-22)")
    parser.add_argument("--save", action="store_true", help="Сохранить отчёт в logs/")
    parser.add_argument("--no-files", action="store_true", help="Не показывать список файлов")
    args = parser.parse_args()

    commits = get_commits(n=args.n, since=args.since)
    if not commits:
        print("Коммиты не найдены.")
        sys.exit(0)

    report = format_report(commits, show_files=not args.no_files)
    print(report)

    if args.save:
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)
        filename = logs_dir / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filename.write_text(report, encoding="utf-8")
        print(f"\nОтчёт сохранён: {filename}")


if __name__ == "__main__":
    main()
