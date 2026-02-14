#!/usr/bin/env python3
"""
Task Lifecycle Manager for unified-taskflow v4.1

Manages task lifecycle: new, complete, abandon, resume, list, status,
suspend, validate, sync-mirror, summary.

Usage:
    python task-lifecycle.py new <task-name>
    python task-lifecycle.py complete [--message "completion note"]
    python task-lifecycle.py abandon [--reason "reason"]
    python task-lifecycle.py resume <archive-name-or-suspended-task>
    python task-lifecycle.py list [--active|--archive]
    python task-lifecycle.py status
    python task-lifecycle.py suspend
    python task-lifecycle.py validate
    python task-lifecycle.py sync-mirror
    python task-lifecycle.py summary
"""

import json
import re
import sys
import os
import shutil
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


def ensure_structure(root: Path) -> None:
    """Ensure .taskflow directory structure exists."""
    (root / ACTIVE_DIR).mkdir(parents=True, exist_ok=True)
    (root / ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)

    index_path = root / INDEX_FILE
    if not index_path.exists():
        index_path.write_text(json.dumps({
            "version": "4.1",
            "tasks": []
        }, indent=2, ensure_ascii=False), encoding='utf-8')


def load_index(root: Path) -> Dict:
    """Load task index."""
    index_path = root / INDEX_FILE
    if index_path.exists():
        return json.loads(index_path.read_text(encoding='utf-8'))
    return {"version": "4.1", "tasks": []}


def save_index(root: Path, index: Dict) -> None:
    """Save task index."""
    index_path = root / INDEX_FILE
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding='utf-8')


def get_active_task(root: Path) -> Optional[str]:
    """Get current active task name (status=active in index), if any."""
    index = load_index(root)
    for t in index["tasks"]:
        if t.get("status") == "active":
            task_dir = root / ACTIVE_DIR / t["name"]
            if task_dir.is_dir():
                return t["name"]
    return None


def get_suspended_task(root: Path) -> Optional[str]:
    """Get current suspended task name, if any."""
    index = load_index(root)
    for t in index["tasks"]:
        if t.get("status") == "suspended":
            task_dir = root / ACTIVE_DIR / t["name"]
            if task_dir.is_dir():
                return t["name"]
    return None


def get_next_version(index: Dict, task_name: str) -> int:
    """Get next version number for a task."""
    versions = [t.get("version", 1) for t in index["tasks"] if t["name"] == task_name]
    return max(versions, default=0) + 1


def new_task(task_name: str) -> bool:
    """Create a new task with anchor.md and checkpoint.md."""
    root = get_taskflow_root()
    ensure_structure(root)

    active = get_active_task(root)
    if active:
        print(f"错误：已有活跃任务 '{active}'")
        print("   请先完成或归档当前任务：")
        print(f"   python task-lifecycle.py complete")
        print(f"   python task-lifecycle.py abandon")
        print(f"   python task-lifecycle.py suspend")
        return False

    task_dir = root / ACTIVE_DIR / task_name
    task_dir.mkdir(parents=True, exist_ok=True)

    # Create anchor.md
    (task_dir / "anchor.md").write_text(
        "# Grounding Anchor\n\n"
        "**Version**: v1\n"
        "**Intent**: \n\n"
        "## Critical Constraints（硬约束 — 违反即失败）\n\n"
        "- \n\n"
        "## Soft Preferences（软偏好 — 尽量满足）\n\n"
        "- \n\n"
        "## Scope\n\n"
        "- Include: \n- Exclude: \n\n"
        "## Done-when（按优先级排序）\n\n"
        "- **P0**: \n- **P1**: \n\n"
        "## Assumptions（假设登记簿）\n\n"
        "| 假设 | 用户确认 |\n|------|----------|\n\n"
        "## Resolved Ambiguities\n\n"
        "- \n\n"
        "## Change Log\n\n"
        "| 版本 | 变更内容 | 原因 |\n|------|----------|------|\n"
        "| v1 | 初始创建 | Phase 0 完成 |\n",
        encoding='utf-8'
    )

    # Create checkpoint.md
    (task_dir / "checkpoint.md").write_text(
        "# 校验点记录\n\n"
        "## Anchor Mirror（每次 checkpoint 更新时刷新）\n\n"
        "- **Intent**: [从 anchor.md 复制]\n"
        "- **Critical Constraints**: [从 anchor.md 复制硬约束]\n"
        "- **Anchor Version**: v1\n\n"
        "## Trace Stub\n\n"
        "**目标**：\n**当前假设**：\n**已排除**：\n\n"
        "---\n\n"
        "## 校验点日志\n\n"
        "> **滚动压缩规则**：保留最近 N 条完整记录（默认 N=3，复杂任务可调至 5）。\n\n"
        "### 历史摘要\n\n"
        "---\n\n"
        "## Debug 记录\n\n"
        "| 问题 | Strike | 尝试方案 | 结果 |\n"
        "|------|--------|----------|------|\n",
        encoding='utf-8'
    )

    # Update index
    index = load_index(root)
    version = get_next_version(index, task_name)
    index["tasks"].append({
        "name": task_name,
        "version": version,
        "status": "active",
        "created": datetime.now().isoformat(),
        "completed": None
    })
    save_index(root, index)

    print(f"已创建任务 '{task_name}'")
    print(f"   目录: {task_dir}")
    print(f"   文件: anchor.md, checkpoint.md")
    return True


def complete_task(message: str = "") -> bool:
    """Complete and archive the active task."""
    root = get_taskflow_root()
    ensure_structure(root)

    active = get_active_task(root)
    if not active:
        print("错误：没有活跃任务")
        return False

    index = load_index(root)
    task_entry = next((t for t in index["tasks"] if t["name"] == active and t["status"] == "active"), None)

    if not task_entry:
        print("错误：索引不一致")
        return False

    # Generate archive name
    date_str = datetime.now().strftime("%Y-%m-%d")
    version = task_entry.get("version", 1)
    archive_name = f"{date_str}_{active}_v{version}"

    # Move to archive
    src = root / ACTIVE_DIR / active
    dst = root / ARCHIVE_DIR / archive_name
    shutil.move(str(src), str(dst))

    # Add completion note if provided
    if message:
        (dst / "COMPLETED.md").write_text(f"# 完成说明\n\n{message}\n", encoding='utf-8')

    # Update index
    task_entry["status"] = "completed"
    task_entry["completed"] = datetime.now().isoformat()
    task_entry["archive_path"] = archive_name
    save_index(root, index)

    print(f"已归档任务 '{active}'")
    print(f"   归档路径: {dst}")
    return True


def abandon_task(reason: str = "") -> bool:
    """Abandon and archive the active task."""
    root = get_taskflow_root()
    ensure_structure(root)

    active = get_active_task(root)
    if not active:
        print("错误：没有活跃任务")
        return False

    index = load_index(root)
    task_entry = next((t for t in index["tasks"] if t["name"] == active and t["status"] == "active"), None)

    if not task_entry:
        print("错误：索引不一致")
        return False

    # Generate archive name
    date_str = datetime.now().strftime("%Y-%m-%d")
    version = task_entry.get("version", 1)
    archive_name = f"{date_str}_{active}_v{version}_ABANDONED"

    # Move to archive
    src = root / ACTIVE_DIR / active
    dst = root / ARCHIVE_DIR / archive_name
    shutil.move(str(src), str(dst))

    # Add abandonment note
    (dst / "ABANDONED.md").write_text(f"# 放弃说明\n\n{reason or '无'}\n", encoding='utf-8')

    # Update index
    task_entry["status"] = "abandoned"
    task_entry["completed"] = datetime.now().isoformat()
    task_entry["archive_path"] = archive_name
    save_index(root, index)

    print(f"已放弃任务 '{active}'")
    print(f"   归档路径: {dst}")
    return True


def suspend_task() -> bool:
    """Suspend the active task (stays in active/, index status -> suspended)."""
    root = get_taskflow_root()
    ensure_structure(root)

    active = get_active_task(root)
    if not active:
        print("错误：没有活跃任务可暂停")
        return False

    # Check if there's already a suspended task
    suspended = get_suspended_task(root)
    if suspended:
        print(f"错误：已有暂停任务 '{suspended}'")
        print("   active/ 下最多一个 active + 一个 suspended")
        print("   请先恢复或归档暂停任务")
        return False

    index = load_index(root)
    task_entry = next((t for t in index["tasks"] if t["name"] == active and t["status"] == "active"), None)
    if not task_entry:
        print("错误：索引不一致")
        return False

    task_entry["status"] = "suspended"
    task_entry["suspended_at"] = datetime.now().isoformat()
    save_index(root, index)

    print(f"已暂停任务 '{active}'")
    print(f"   任务保留在 active/ 下，状态标记为 suspended")
    print(f"   使用 'python task-lifecycle.py resume {active}' 恢复")
    return True


def resume_task(task_name: str) -> bool:
    """Resume a suspended task or restore an archived task."""
    root = get_taskflow_root()
    ensure_structure(root)

    index = load_index(root)

    # First, check if it's a suspended task in active/
    suspended_entry = next(
        (t for t in index["tasks"] if t["name"] == task_name and t["status"] == "suspended"),
        None
    )

    if suspended_entry:
        # Check if there's already an active task
        active = get_active_task(root)
        if active:
            print(f"错误：已有活跃任务 '{active}'")
            print("   请先完成、暂停或归档当前任务")
            return False

        suspended_entry["status"] = "active"
        suspended_entry.pop("suspended_at", None)
        save_index(root, index)

        print(f"已恢复暂停任务 '{task_name}'")
        return True

    # Otherwise, try to restore from archive (legacy behavior)
    archive_dir = root / ARCHIVE_DIR / task_name
    if not archive_dir.exists():
        print(f"错误：未找到任务 '{task_name}'（既非暂停任务也非归档任务）")
        return False

    active = get_active_task(root)
    if active:
        print(f"错误：已有活跃任务 '{active}'")
        return False

    # Restore from archive
    original_name = task_name.split("_", 1)[-1] if "_" in task_name else task_name
    # Remove version suffix and date prefix
    parts = task_name.split("_")
    if len(parts) >= 3:
        original_name = "_".join(parts[1:-1])

    dst = root / ACTIVE_DIR / original_name
    shutil.move(str(archive_dir), str(dst))

    # Update index
    task_entry = next((t for t in index["tasks"] if t.get("archive_path") == task_name), None)
    if task_entry:
        task_entry["status"] = "active"
        task_entry["completed"] = None
        save_index(root, index)

    print(f"已恢复任务 '{original_name}'")
    print(f"   目录: {dst}")
    return True


def list_tasks(show_active: bool = True, show_archive: bool = True) -> None:
    """List tasks."""
    root = get_taskflow_root()
    ensure_structure(root)

    index = load_index(root)

    if show_active:
        active = get_active_task(root)
        suspended = get_suspended_task(root)
        print("活跃任务:")
        if active:
            entry = next((t for t in index["tasks"] if t["name"] == active and t["status"] == "active"), {})
            created = entry.get("created", "unknown")[:10]
            print(f"   [active] {active} (创建: {created})")
        else:
            print("   (无)")

        print("\n暂停任务:")
        if suspended:
            entry = next((t for t in index["tasks"] if t["name"] == suspended and t["status"] == "suspended"), {})
            suspended_at = entry.get("suspended_at", "unknown")[:16]
            print(f"   [suspended] {suspended} (暂停于: {suspended_at})")
        else:
            print("   (无)")

    if show_archive:
        print("\n归档任务:")
        archived = [t for t in index["tasks"] if t["status"] in ("completed", "abandoned")]
        if archived:
            for t in archived[-10:]:  # Show last 10
                status = "[完成]" if t["status"] == "completed" else "[放弃]"
                print(f"   {status} {t.get('archive_path', t['name'])}")
        else:
            print("   (无)")


def show_status() -> None:
    """Show current status."""
    root = get_taskflow_root()

    if not root.exists():
        print("未初始化 .taskflow 目录")
        print("   运行 'python task-lifecycle.py new <task-name>' 开始")
        return

    active = get_active_task(root)
    suspended = get_suspended_task(root)
    index = load_index(root)

    print(f"目录: {root}")
    print(f"活跃任务: {active or '(无)'}")
    print(f"暂停任务: {suspended or '(无)'}")
    print(f"归档数量: {len([t for t in index['tasks'] if t['status'] not in ('active', 'suspended')])}")


def _get_active_task_dir(root: Path) -> Optional[Path]:
    """Get the directory of the active task, or None."""
    active = get_active_task(root)
    if not active:
        return None
    return root / ACTIVE_DIR / active


def _parse_anchor(anchor_path: Path) -> Dict:
    """Parse anchor.md and extract key fields."""
    result = {
        "version": None,
        "intent": None,
        "critical_constraints": [],
        "scope": None,
        "done_when": [],
        "raw": ""
    }

    if not anchor_path.exists():
        return result

    content = anchor_path.read_text(encoding='utf-8')
    result["raw"] = content

    # Extract Version
    m = re.search(r'\*\*Version\*\*:\s*v(\d+)', content)
    if m:
        result["version"] = f"v{m.group(1)}"

    # Extract Intent
    m = re.search(r'\*\*Intent\*\*:\s*(.+)', content)
    if m:
        result["intent"] = m.group(1).strip()

    # Extract Critical Constraints section
    cc_match = re.search(
        r'## Critical Constraints[^\n]*\n(.*?)(?=\n## |\Z)',
        content, re.DOTALL
    )
    if cc_match:
        lines = cc_match.group(1).strip().split('\n')
        result["critical_constraints"] = [
            l.strip().lstrip('- ') for l in lines if l.strip().startswith('-')
        ]

    # Extract Scope
    scope_match = re.search(
        r'## Scope\n(.*?)(?=\n## |\Z)',
        content, re.DOTALL
    )
    if scope_match:
        result["scope"] = scope_match.group(1).strip()

    # Extract Done-when
    dw_match = re.search(
        r'## Done-when[^\n]*\n(.*?)(?=\n## |\Z)',
        content, re.DOTALL
    )
    if dw_match:
        lines = dw_match.group(1).strip().split('\n')
        result["done_when"] = [
            l.strip().lstrip('- ') for l in lines if l.strip().startswith('-')
        ]

    return result


def _parse_checkpoint_mirror_version(checkpoint_path: Path) -> Optional[str]:
    """Extract Anchor Version from checkpoint.md's Anchor Mirror."""
    if not checkpoint_path.exists():
        return None

    content = checkpoint_path.read_text(encoding='utf-8')
    m = re.search(r'\*\*Anchor Version\*\*:\s*v(\d+)', content)
    if m:
        return f"v{m.group(1)}"
    return None


def _count_strikes(checkpoint_path: Path) -> int:
    """Count max strike in Debug section of checkpoint.md."""
    if not checkpoint_path.exists():
        return 0

    content = checkpoint_path.read_text(encoding='utf-8')
    strikes = re.findall(r'\|\s*\d+\s*\|', content)
    if not strikes:
        return 0
    nums = [int(re.search(r'\d+', s).group()) for s in strikes]
    return max(nums) if nums else 0


def validate_task() -> bool:
    """Validate the active task's file integrity."""
    root = get_taskflow_root()
    ensure_structure(root)

    task_dir = _get_active_task_dir(root)
    if not task_dir:
        print("错误：没有活跃任务")
        return False

    active = get_active_task(root)
    anchor_path = task_dir / "anchor.md"
    checkpoint_path = task_dir / "checkpoint.md"

    issues = []  # (level, message)

    print(f"校验任务 '{active}'...")
    print()

    # 1. Check anchor.md exists and has required fields
    if not anchor_path.exists():
        issues.append(("FAIL", "anchor.md 不存在"))
    else:
        anchor = _parse_anchor(anchor_path)

        if not anchor["version"]:
            issues.append(("FAIL", "anchor.md 缺少 Version 字段"))
        if not anchor["intent"] or anchor["intent"] in ("[一句话描述用户核心意图]", ""):
            issues.append(("FAIL", "anchor.md 缺少 Intent 字段"))
        if not anchor["critical_constraints"] or all(c == "" for c in anchor["critical_constraints"]):
            issues.append(("WARN", "anchor.md Critical Constraints 为空"))
        if not anchor["scope"]:
            issues.append(("WARN", "anchor.md Scope 为空"))
        if not anchor["done_when"] or all(d == "" for d in anchor["done_when"]):
            issues.append(("FAIL", "anchor.md Done-when 为空"))

    # 2. Check checkpoint.md exists and Anchor Mirror version matches
    if not checkpoint_path.exists():
        issues.append(("FAIL", "checkpoint.md 不存在"))
    else:
        mirror_version = _parse_checkpoint_mirror_version(checkpoint_path)
        if anchor_path.exists():
            anchor = _parse_anchor(anchor_path)
            if anchor["version"] and mirror_version:
                if anchor["version"] != mirror_version:
                    issues.append(("WARN", f"Anchor Mirror 版本不匹配: anchor={anchor['version']}, mirror={mirror_version}"))
                    issues.append(("WARN", "  运行 'python task-lifecycle.py sync-mirror' 修复"))
            elif anchor["version"] and not mirror_version:
                issues.append(("WARN", "checkpoint.md 的 Anchor Mirror 缺少版本标记"))

    # 3. Check Strike count
    if checkpoint_path.exists():
        max_strike = _count_strikes(checkpoint_path)
        if max_strike >= 3:
            issues.append(("WARN", f"Debug 记录中 Strike 已达 {max_strike}，应升级给用户"))

    # Output report
    fail_count = sum(1 for level, _ in issues if level == "FAIL")
    warn_count = sum(1 for level, _ in issues if level == "WARN")

    if issues:
        for level, msg in issues:
            print(f"  [{level}] {msg}")
    else:
        print("  [PASS] 所有检查通过")

    print()
    if fail_count > 0:
        print(f"结果: FAIL ({fail_count} 个错误, {warn_count} 个警告)")
        return False
    elif warn_count > 0:
        print(f"结果: WARN ({warn_count} 个警告)")
        return True
    else:
        print("结果: PASS")
        return True


def sync_mirror() -> bool:
    """Sync Anchor Mirror in checkpoint.md from anchor.md."""
    root = get_taskflow_root()
    ensure_structure(root)

    task_dir = _get_active_task_dir(root)
    if not task_dir:
        print("错误：没有活跃任务")
        return False

    anchor_path = task_dir / "anchor.md"
    checkpoint_path = task_dir / "checkpoint.md"

    if not anchor_path.exists():
        print("错误：anchor.md 不存在")
        return False
    if not checkpoint_path.exists():
        print("错误：checkpoint.md 不存在")
        return False

    anchor = _parse_anchor(anchor_path)
    if not anchor["version"]:
        print("错误：anchor.md 缺少 Version 字段")
        return False

    intent = anchor["intent"] or "[未填写]"
    constraints = anchor["critical_constraints"]
    constraints_str = "; ".join(constraints) if constraints else "[未填写]"
    version = anchor["version"]

    # Read checkpoint.md
    cp_content = checkpoint_path.read_text(encoding='utf-8')

    # Replace Anchor Mirror block
    # Pattern: from "## Anchor Mirror" to next "##" section
    mirror_pattern = re.compile(
        r'(## Anchor Mirror[^\n]*\n)(.*?)(?=\n## )',
        re.DOTALL
    )

    new_mirror = (
        f"\n> 从 anchor.md 复制核心约束，利用首位效应防止遗忘\n\n"
        f"- **Intent**: {intent}\n"
        f"- **Critical Constraints**: {constraints_str}\n"
        f"- **Anchor Version**: {version}\n"
    )

    if mirror_pattern.search(cp_content):
        cp_content = mirror_pattern.sub(r'\1' + new_mirror, cp_content)
    else:
        print("警告：未找到 Anchor Mirror 区块，跳过更新")
        return False

    checkpoint_path.write_text(cp_content, encoding='utf-8')

    print(f"已同步 Anchor Mirror:")
    print(f"   Intent: {intent}")
    print(f"   Critical Constraints: {constraints_str}")
    print(f"   Anchor Version: {version}")
    return True


def summary_task() -> bool:
    """Generate compact summary from anchor.md to stdout."""
    root = get_taskflow_root()
    ensure_structure(root)

    task_dir = _get_active_task_dir(root)
    if not task_dir:
        print("错误：没有活跃任务")
        return False

    anchor_path = task_dir / "anchor.md"
    if not anchor_path.exists():
        print("错误：anchor.md 不存在")
        return False

    anchor = _parse_anchor(anchor_path)
    active = get_active_task(root)

    print(f"Task: {active}")
    print(f"Intent: {anchor['intent'] or '[未填写]'}")
    print(f"Version: {anchor['version'] or '[未填写]'}")
    print()

    if anchor["critical_constraints"]:
        print("Critical Constraints:")
        for c in anchor["critical_constraints"]:
            if c:
                print(f"  - {c}")

    # Show only P0 Done-when
    p0_items = [d for d in anchor["done_when"] if d.startswith("**P0**") or d.startswith("P0")]
    if p0_items:
        print()
        print("P0 Done-when:")
        for item in p0_items:
            print(f"  - {item}")

    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]

    if action == "new":
        if len(sys.argv) < 3:
            print("用法: python task-lifecycle.py new <task-name>")
            sys.exit(1)
        task_name = sys.argv[2]
        success = new_task(task_name)
        sys.exit(0 if success else 1)

    elif action == "complete":
        message = ""
        if "--message" in sys.argv:
            idx = sys.argv.index("--message")
            if idx + 1 < len(sys.argv):
                message = sys.argv[idx + 1]
        success = complete_task(message)
        sys.exit(0 if success else 1)

    elif action == "abandon":
        reason = ""
        if "--reason" in sys.argv:
            idx = sys.argv.index("--reason")
            if idx + 1 < len(sys.argv):
                reason = sys.argv[idx + 1]
        success = abandon_task(reason)
        sys.exit(0 if success else 1)

    elif action == "suspend":
        success = suspend_task()
        sys.exit(0 if success else 1)

    elif action == "resume":
        if len(sys.argv) < 3:
            print("用法: python task-lifecycle.py resume <task-name-or-archive-name>")
            sys.exit(1)
        task_name = sys.argv[2]
        success = resume_task(task_name)
        sys.exit(0 if success else 1)

    elif action == "validate":
        success = validate_task()
        sys.exit(0 if success else 1)

    elif action == "sync-mirror":
        success = sync_mirror()
        sys.exit(0 if success else 1)

    elif action == "summary":
        success = summary_task()
        sys.exit(0 if success else 1)

    elif action == "list":
        show_active = "--archive" not in sys.argv
        show_archive = "--active" not in sys.argv
        list_tasks(show_active, show_archive)

    elif action == "status":
        show_status()

    else:
        print(f"未知操作: {action}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
