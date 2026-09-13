import json
import os
import re
from pathlib import Path

def run_audit():
    dsh_home = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
    profile_web = dsh_home / "profiles" / "web"
    base_dir = profile_web / "base-dsh-0.1.1-rc.2"

    print("==================================================")
    print("1. DSH HOST / BOOT / FRONTEND / CONVERSATION / CORDIS VERSIONS")
    print("==================================================")

    # Base package
    dsh_pkg = base_dir / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
    if dsh_pkg.is_file():
        d = json.loads(dsh_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Host Package: {d.get('name')} @ {d.get('version')}")

    # Cordis package
    cordis_pkg = base_dir / "node_modules" / "@deepseek-ai" / "cordis" / "package.json"
    if not cordis_pkg.is_file():
        cordis_pkg = profile_web / "node_modules" / "@deepseek-ai" / "cordis" / "package.json"
    if not cordis_pkg.is_file():
        cordis_pkg = dsh_home / "profiles" / "node_modules" / "@deepseek-ai" / "cordis" / "package.json"
    if cordis_pkg.is_file():
        d = json.loads(cordis_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Cordis Core: {d.get('name')} @ {d.get('version')} ({cordis_pkg})")

    # Client UI Conversation package
    ui_conv_pkg = base_dir / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "package.json"
    if ui_conv_pkg.is_file():
        d = json.loads(ui_conv_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Conversation Plugin: {d.get('name')} @ {d.get('version')} ({ui_conv_pkg})")
    nested_ui_conv_pkg = base_dir / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "package.json"
    if nested_ui_conv_pkg.is_file():
        d = json.loads(nested_ui_conv_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Conversation Plugin (nested): {d.get('name')} @ {d.get('version')} ({nested_ui_conv_pkg})")

    # Web Frontend package
    frontend_pkg = base_dir / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "package.json"
    if frontend_pkg.is_file():
        d = json.loads(frontend_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Web Frontend: {d.get('name')} @ {d.get('version')} ({frontend_pkg})")
    nested_frontend_pkg = base_dir / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "package.json"
    if nested_frontend_pkg.is_file():
        d = json.loads(nested_frontend_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Web Frontend (nested): {d.get('name')} @ {d.get('version')} ({nested_frontend_pkg})")

    print("\n==================================================")
    print("2. PHYSICAL EVIDENCE OF 0.1.2-alpha.3 INCIDENT ARTIFACTS")
    print("==================================================")
    backup_ui_conv = dsh_home / ".aic-dsh-backups" / "dsh-95e82206850140b696540740f60ceab1" / "profiles" / "web" / "base-dsh-0.1.1-rc.2" / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "package.json"
    if backup_ui_conv.is_file():
        d = json.loads(backup_ui_conv.read_text(encoding="utf-8", errors="ignore"))
        print(f"Incident Backup Conversation: {d.get('name')} @ {d.get('version')}")
        cordis_block = d.get("@deepseek-ai/cordis", d.get("cordis", {}))
        print(f"  Incident Required Services: {cordis_block.get('services', {}).get('required')}")
        print(f"  Incident Provided Services: {cordis_block.get('services', {}).get('provided')}")

    backup_frontend = dsh_home / ".aic-dsh-backups" / "dsh-95e82206850140b696540740f60ceab1" / "profiles" / "web" / "base-dsh-0.1.1-rc.2" / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "package.json"
    if backup_frontend.is_file():
        d = json.loads(backup_frontend.read_text(encoding="utf-8", errors="ignore"))
        print(f"Incident Backup Frontend: {d.get('name')} @ {d.get('version')}")

    worktree_pkg = dsh_home / "cache" / "sources" / "dsh-ui-sqt_wwd_" / "package.json"
    if worktree_pkg.is_file():
        d = json.loads(worktree_pkg.read_text(encoding="utf-8", errors="ignore"))
        print(f"Source Worktree Root Package: {d.get('name')} @ {d.get('version')}")

    print("\n==================================================")
    print("3. CURRENT RESTORED PACKAGES REQUIRED/PROVIDED SERVICES")
    print("==================================================")
    if ui_conv_pkg.is_file():
        d = json.loads(ui_conv_pkg.read_text(encoding="utf-8", errors="ignore"))
        cordis_block = d.get("@deepseek-ai/cordis", d.get("cordis", {}))
        print(f"Current Restored Conversation ({d.get('version')}):")
        print(f"  required: {cordis_block.get('services', {}).get('required')}")
        print(f"  provided: {cordis_block.get('services', {}).get('provided')}")

    print("\n==================================================")
    print("4. ALL CORE DSH PACKAGES & OVERLAYS AUDIT")
    print("==================================================")
    # Check all packages in base
    deepseek_dir = base_dir / "node_modules" / "@deepseek-ai"
    all_packages = {}
    if deepseek_dir.exists():
        for p in sorted(deepseek_dir.iterdir()):
            pj = p / "package.json"
            if pj.is_file():
                try:
                    data = json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
                    cordis = data.get("@deepseek-ai/cordis", data.get("cordis", {}))
                    all_packages[data.get("name", p.name)] = {
                        "version": data.get("version"),
                        "path": str(p.relative_to(dsh_home)),
                        "required": cordis.get("services", {}).get("required", []),
                        "provided": cordis.get("services", {}).get("provided", [])
                    }
                except Exception:
                    pass

    # Check overlays
    overlays_dir = profile_web / "plugins"
    if overlays_dir.exists():
        for p in sorted(overlays_dir.iterdir()):
            if p.is_dir():
                pj = p / "package.json"
                if pj.is_file():
                    try:
                        data = json.loads(pj.read_text(encoding="utf-8", errors="ignore"))
                        cordis = data.get("@deepseek-ai/cordis", data.get("cordis", {}))
                        all_packages[data.get("name", p.name) + " (overlay)"] = {
                            "version": data.get("version"),
                            "path": str(p.relative_to(dsh_home)),
                            "required": cordis.get("services", {}).get("required", []),
                            "provided": cordis.get("services", {}).get("provided", [])
                        }
                    except Exception:
                        pass

    for name, info in sorted(all_packages.items()):
        print(f"{name}: {info['version']} | path: {info['path']}")
        if info['required']:
            print(f"   required: {info['required']}")
        if info['provided']:
            print(f"   provided: {info['provided']}")

if __name__ == "__main__":
    run_audit()
