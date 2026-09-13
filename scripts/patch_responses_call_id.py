import re
from pathlib import Path

def patch_file(p: Path):
    if not p.is_file():
        return False
    text = p.read_text(encoding='utf-8')
    
    # 1. normalizeIdPart
    old_norm = (
        '    const normalizeIdPart = (part) => {\n'
        '        const sanitized = part.replace(/[^a-zA-Z0-9_-]/g, "_");\n'
        '        const normalized = sanitized.length > 64 ? sanitized.slice(0, 64) : sanitized;\n'
        '        return normalized.replace(/_+$/, "");\n'
        '    };'
    )
    new_norm = (
        '    const normalizeIdPart = (part) => {\n'
        '        const sanitized = part.replace(/[^a-zA-Z0-9_-]/g, "_");\n'
        '        if (sanitized.length <= 64) {\n'
        '            return sanitized.replace(/_+$/, "");\n'
        '        }\n'
        '        const hash = shortHash(part).slice(0, 8);\n'
        '        const prefix = sanitized.slice(0, Math.max(1, 64 - hash.length - 1)).replace(/_+$/, "");\n'
        '        return prefix + "_" + hash;\n'
        '    };'
    )
    
    # 2. toolCall
    old_tc = (
        '                    const toolCall = block;\n'
        '                    const [callId, itemIdRaw] = toolCall.id.split("|");'
    )
    new_tc = (
        '                    const toolCall = block;\n'
        '                    const [callIdRaw, itemIdRaw] = toolCall.id.split("|");\n'
        '                    const callId = normalizeIdPart(callIdRaw);'
    )
    
    # 3. toolResult
    old_tr = (
        '        else if (msg.role === "toolResult") {\n'
        '            const [callId] = msg.toolCallId.split("|");'
    )
    new_tr = (
        '        else if (msg.role === "toolResult") {\n'
        '            const [callIdRaw] = msg.toolCallId.split("|");\n'
        '            const callId = normalizeIdPart(callIdRaw);'
    )
    
    if old_norm in text:
        text = text.replace(old_norm, new_norm, 1)
    if old_tc in text:
        text = text.replace(old_tc, new_tc, 1)
    if old_tr in text:
        text = text.replace(old_tr, new_tr, 1)
        
    p.write_text(text, encoding='utf-8')
    return True

import os
import sys

TARGET_BASENAME = Path(
    "node_modules/@earendil-works/pi-ai/dist/api/openai-responses-shared.js")


def discover_targets() -> list[Path]:
    """Search DSH profiles + npm cache for the patch target (no hardcoded paths)."""
    roots = [
        Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")) / "profiles",
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "npm-cache" / "_npx",
    ]
    found = []
    for root in roots:
        if root.is_dir():
            found.extend(root.rglob("openai-responses-shared.js"))
    return sorted(found)


if __name__ == "__main__":
    targets = [Path(a) for a in sys.argv[1:]] or discover_targets()
    if not targets:
        print("no targets found — pass paths as argv or set DSH_HOME")
        sys.exit(1)
    for tp in targets:
        print(f"Patched {tp.name}: {patch_file(tp)}")
