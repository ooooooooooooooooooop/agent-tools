#!/usr/bin/env python3
"""
Task Lifecycle Manager for unified-taskflow v4.3

Manages task lifecycle: new, complete, abandon, resume, list, status,
suspend, validate, sync-mirror, summary.

Usage:
    python task-lifecycle.py [--project-path <path>] [--json] new <task-name>
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
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List


TASKFLOW_DIR = ".taskflow"
ACTIVE_DIR = "active"
ARCHIVE_DIR = "archive"
INDEX_FILE = "index.json"
TASKFLOW_VERSION = "4.3"
ARCHIVED_STATUSES = ("completed", "abandoned", "archived")
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR.parent / "assets" / "templates"
PROJECT_PATH = "."
OUTPUT_JSON = False


def get_taskflow_root(project_path: Optional[str] = None) -> Path:
    """Get the .taskflow directory path."""
    return Path(project_path or PROJECT_PATH).resolve() / TASKFLOW_DIR


def atomic_write_text(path: Path, content: str) -> None:
    """Write a UTF-8 file without exposing a partially written result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def load_template(name: str) -> str:
    """Load a checked-in package template instead of duplicating it in code."""
    template_path = TEMPLATE_DIR / name
    if not template_path.is_file():
        raise FileNotFoundError(f"taskflow template is missing: {template_path}")
    return template_path.read_text(encoding="utf-8")


def validate_task_name(task_name: str) -> bool:
    """Allow only a safe single directory component for active task names."""
    return bool(TASK_NAME_RE.fullmatch(task_name))


def safe_child(parent: Path, name: str) -> Optional[Path]:
    """Resolve a user-supplied child name without allowing path traversal."""
    if not name or Path(name).name != name:
        return None
    candidate = (parent / name).resolve()
    try:
        candidate.relative_to(parent.resolve())
    except ValueError:
        return None
    return candidate


def validate_index(index: Dict) -> List[str]:
    """Return schema and lifecycle consistency issues for index.json."""
    issues: List[str] = []
    if not isinstance(index, dict):
        return ["index.json must contain an object"]
    if not isinstance(index.get("tasks"), list):
        return ["index.json tasks must be an array"]
    active_count = 0
    suspended_count = 0
    live_names = set()
    allowed = {"active", "suspended", *ARCHIVED_STATUSES}
    for item in index["tasks"]:
        if not isinstance(item, dict):
            issues.append("index.json contains a non-object task")
            continue
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not validate_task_name(name):
            issues.append(f"unsafe or invalid task name: {name!r}")
        elif status in {"active", "suspended"} and name in live_names:
            issues.append(f"duplicate live task name in index: {name}")
        elif status in {"active", "suspended"}:
            live_names.add(name)
        if status not in allowed:
            issues.append(f"invalid task status for {name!r}: {status!r}")
        elif status == "active":
            active_count += 1
        elif status == "suspended":
            suspended_count += 1
    if active_count > 1:
        issues.append("index.json contains more than one active task")
    if suspended_count > 1:
        issues.append("index.json contains more than one suspended task")
    return issues


def ensure_structure(root: Path) -> None:
    """Ensure .taskflow directory structure exists."""
    (root / ACTIVE_DIR).mkdir(parents=True, exist_ok=True)
    (root / ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)

    index_path = root / INDEX_FILE
    if not index_path.exists():
        atomic_write_text(index_path, json.dumps({
            "version": TASKFLOW_VERSION,
            "tasks": []
        }, indent=2, ensure_ascii=False) + "\n")


def load_index(root: Path) -> Dict:
    """Load task index."""
    index_path = root / INDEX_FILE
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding='utf-8'))
        issues = validate_index(index)
        if issues:
            raise ValueError("; ".join(issues))
        return index
    return {"version": TASKFLOW_VERSION, "tasks": []}


def save_index(root: Path, index: Dict) -> None:
    """Save task index."""
    issues = validate_index(index)
    if issues:
        raise ValueError("; ".join(issues))
    index_path = root / INDEX_FILE
    atomic_write_text(index_path, json.dumps(index, indent=2, ensure_ascii=False) + "\n")


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
    if not validate_task_name(task_name):
        print(f"错误：任务名不安全或格式无效 '{task_name}'（仅允许字母、数字、-、_，且不超过 64 个字符）")
        return False
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
    if task_dir.exists():
        print(f"错误：活跃目录已存在，拒绝覆盖 '{task_name}'")
        return False
    task_dir.mkdir(parents=True, exist_ok=False)

    # Templates are package resources and remain the single source of truth.
    atomic_write_text(task_dir / "anchor.md", load_template("anchor.md"))
    atomic_write_text(task_dir / "checkpoint.md", load_template("checkpoint.md").replace("v??", "v1"))

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
    if dst.exists():
        print(f"错误：归档目标已存在，拒绝覆盖 '{dst}'")
        return False
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
    if dst.exists():
        print(f"错误：归档目标已存在，拒绝覆盖 '{dst}'")
        return False
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
    archive_dir = safe_child(root / ARCHIVE_DIR, task_name)
    if archive_dir is None or not archive_dir.exists():
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

    if not validate_task_name(original_name):
        print(f"错误：归档任务名无法安全恢复 '{original_name}'")
        return False
    dst = safe_child(root / ACTIVE_DIR, original_name)
    if dst is None or dst.exists():
        print(f"错误：恢复目标已存在或路径不安全 '{original_name}'")
        return False
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
    active = get_active_task(root)
    suspended = get_suspended_task(root)
    archived = [t for t in index["tasks"] if t["status"] in ARCHIVED_STATUSES]

    if OUTPUT_JSON:
        print(json.dumps({
            "root": str(root),
            "active": active,
            "suspended": suspended,
            "archive": archived[-10:],
        }, ensure_ascii=False, indent=2))
        return

    if show_active:
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
        if archived:
            for t in archived[-10:]:  # Show last 10
                status = {
                    "completed": "[完成]",
                    "abandoned": "[放弃]",
                    "archived": "[归档]",
                }[t["status"]]
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

    if OUTPUT_JSON:
        print(json.dumps({
            "root": str(root),
            "active": active,
            "suspended": suspended,
            "archive_count": len([t for t in index["tasks"] if t["status"] not in ("active", "suspended")]),
        }, ensure_ascii=False, indent=2))
        return

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

    try:
        index_issues = validate_index(load_index(root))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误：index.json 无法校验：{exc}")
        return False
    if index_issues:
        for issue in index_issues:
            print(f"  [FAIL] {issue}")
        return False

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


def parse_global_options(argv: List[str]) -> List[str]:
    """Parse options accepted before or after the lifecycle action."""
    global PROJECT_PATH, OUTPUT_JSON
    remaining: List[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--json":
            OUTPUT_JSON = True
        elif item == "--project-path":
            if index + 1 >= len(argv):
                raise ValueError("--project-path 需要目录参数")
            PROJECT_PATH = argv[index + 1]
            index += 1
        elif item.startswith("--project-path="):
            PROJECT_PATH = item.split("=", 1)[1]
        else:
            remaining.append(item)
        index += 1
    return remaining


def main():
    if any(item in {"--help", "-h"} for item in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)

    try:
        argv = parse_global_options(sys.argv[1:])
    except ValueError as exc:
        print(f"错误：{exc}")
        sys.exit(1)

    if not argv:
        print(__doc__)
        sys.exit(1)

    action = argv[0]

    if action == "new":
        if len(argv) < 2:
            print("用法: python task-lifecycle.py new <task-name>")
            sys.exit(1)
        task_name = argv[1]
        success = new_task(task_name)
        sys.exit(0 if success else 1)

    elif action == "complete":
        message = ""
        if "--message" in argv:
            idx = argv.index("--message")
            if idx + 1 < len(argv):
                message = argv[idx + 1]
        success = complete_task(message)
        sys.exit(0 if success else 1)

    elif action == "abandon":
        reason = ""
        if "--reason" in argv:
            idx = argv.index("--reason")
            if idx + 1 < len(argv):
                reason = argv[idx + 1]
        success = abandon_task(reason)
        sys.exit(0 if success else 1)

    elif action == "suspend":
        success = suspend_task()
        sys.exit(0 if success else 1)

    elif action == "resume":
        if len(argv) < 2:
            print("用法: python task-lifecycle.py resume <task-name-or-archive-name>")
            sys.exit(1)
        task_name = argv[1]
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
        show_active = "--archive" not in argv
        show_archive = "--active" not in argv
        list_tasks(show_active, show_archive)

    elif action == "status":
        show_status()

    else:
        print(f"未知操作: {action}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
