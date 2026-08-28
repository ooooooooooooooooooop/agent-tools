# PersonalAI Governance — WEEKLY (less frequent): model state/health, dead config,
# stale memory, duplicate rules, project state. Expensive canary stays manual.
$ErrorActionPreference = 'Continue'
$repo = "C:\Users\admin\Desktop\skills"
foreach ($m in 'model_state','model_health','dead_config','memory_gov','dup_rules','project_state_gov','durability_gov') {
  & python "$repo\scripts\governance\$m.py"
}
exit 0
