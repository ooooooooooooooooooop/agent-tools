#!/usr/bin/env python3
"""
Session Catchup Script for unified-taskflow v4.1

Analyzes .taskflow/ directory to recover context from previous sessions.

Usage: python session-catchup.py [project-path]
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List


TASKFLOW_DIR = ".taskflow"
ACTIVE_DIR = "active"
ARCHIVE_DIR = "archive"
INDEX_FILE = "index.json"


def get_taskflow_root(project_path: str = ".") -> Path:
    """Get the .taskflow directory path."""
    return Path(project_path).resolve() / TASKFLOW_DIR


def load_index(root: Path) -> Dict:
    """Load task index."""
    index_path = root / INDEX_FILE
    if index_path.exists():
        return json.loads(index_path.read_text(encoding='utf-8'))
    return {"version": "4.1", "tasks": []}


def get_active_task(root: Path) -> Optional[str]:
    """Get current active task name, if any."""
    index = load_index(root)
    for t in index.get("tasks", []):
        if t.get("status") == "active":
            task_dir = root / ACTIVE_DIR / t["name"]
            if task_dir.is_dir():
                return t["name"]
    return None


def get_suspended_task(root: Path) -> Optional[str]:
    """Get current suspended task name, if any."""
    index = load_index(root)
    for t in index.get("tasks", []):
        if t.get("status") == "suspended":
            task_dir = root / ACTIVE_DIR / t["name"]
            if task_dir.is_dir():
                return t["name"]
    return None


def get_file_summary(file_path: Path, max_lines: int = 20) -> str:
    """Get summary of a file's content."""
    if not file_path.exists():
        return "(文件不存在)"

    try:
        content = file_path.read_text(encoding='utf-8')
        lines = content.split('\n')[:max_lines]
        return '\n'.join(lines)
    except Exception as e:
        return f"(读取失败: {e})"


def detect_task_files(task_dir: Path) -> Dict[str, bool]:
    """Detect which task files exist."""
    return {
        "anchor.md": (task_dir / "anchor.md").exists(),
        "checkpoint.md": (task_dir / "checkpoint.md").exists(),
        "design.md": (task_dir / "design.md").exists(),
    }


def main():
    project_path = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    root = get_taskflow_root(project_path)

    print(f"\n[unified-taskflow v4.1] SESSION CATCHUP")
    print(f"目录: {root}")

    if not root.exists():
        print("\n未检测到 .taskflow/ 目录")
        print("   这可能是新项目或首次使用 unified-taskflow")
        print("   可通过以下命令初始化：")
        print("   python scripts/task-lifecycle.py new <task-name>")
        return

    # Check active task
    active_task = get_active_task(root)
    suspended_task = get_suspended_task(root)

    if active_task:
        task_dir = root / ACTIVE_DIR / active_task
        files = detect_task_files(task_dir)
        index = load_index(root)
        task_entry = next((t for t in index.get("tasks", []) if t.get("name") == active_task and t.get("status") == "active"), {})

        print(f"\n发现活跃任务: {active_task}")
        print(f"   创建时间: {task_entry.get('created', 'unknown')[:16]}")
        print(f"   目录: {task_dir}")
        print(f"   文件: {', '.join(k for k, v in files.items() if v)}")

        # Show key files
        print("\n--- 任务文件 ---")

        # Prioritize anchor.md as the grounding reference
        if files["anchor.md"]:
            print(f"\n[anchor.md] (北极星文件):")
            print(get_file_summary(task_dir / "anchor.md", 20))

        if files["checkpoint.md"]:
            print(f"\n[checkpoint.md] (校验点记录):")
            print(get_file_summary(task_dir / "checkpoint.md", 15))

        if files["design.md"]:
            print(f"\n[design.md] (技术设计):")
            print(get_file_summary(task_dir / "design.md", 15))

        print("\n--- 建议操作 ---")
        print("1. 阅读 anchor.md 恢复任务上下文")
        print("2. 阅读 checkpoint.md 了解执行进度")
        print("3. 运行 'python scripts/task-lifecycle.py validate' 校验文件完整性")
        print("4. 运行 'python scripts/task-lifecycle.py sync-mirror' 同步 Anchor Mirror")
        print("5. 执行 Re-grounding：对齐当前状态与 anchor.md")
        print("6. 继续执行任务")
        print("7. 或运行 'python scripts/task-lifecycle.py complete' 归档")

    # Check suspended task
    if suspended_task:
        task_dir = root / ACTIVE_DIR / suspended_task
        index = load_index(root)
        task_entry = next((t for t in index.get("tasks", []) if t.get("name") == suspended_task and t.get("status") == "suspended"), {})

        print(f"\n发现暂停任务: {suspended_task}")
        print(f"   创建时间: {task_entry.get('created', 'unknown')[:16]}")
        print(f"   暂停时间: {task_entry.get('suspended_at', 'unknown')[:16]}")
        print(f"   目录: {task_dir}")

        if not active_task:
            print("\n--- 建议操作 ---")
            print(f"1. 运行 'python scripts/task-lifecycle.py resume {suspended_task}' 恢复暂停任务")
            print("2. 或运行 'python scripts/task-lifecycle.py new <task-name>' 开始新任务")

    if not active_task and not suspended_task:
        print("\n无活跃或暂停任务")

        # Show recent archives
        index = load_index(root)
        recent = [t for t in index.get("tasks", []) if t.get("status") in ("completed", "abandoned")][-5:]

        if recent:
            print("\n最近归档:")
            for t in recent:
                status = "[完成]" if t.get("status") == "completed" else "[放弃]"
                print(f"   {status} {t.get('archive_path', t.get('name'))}")

        print("\n--- 建议操作 ---")
        print("运行 'python scripts/task-lifecycle.py new <task-name>' 开始新任务")


if __name__ == "__main__":
    main()
