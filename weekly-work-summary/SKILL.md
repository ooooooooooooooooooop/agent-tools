---
name: weekly-work-summary
description: Evidence-based weekly status synthesis for any current workspace or user-specified folder. Use when the user asks for a weekly report, asks what was done recently, asks for a concise day-by-day recap, or gives a vague prompt such as "weekly summary", "status update", "what did we do this week", "本周工作总结", "周报", "总结本周", "这周干了什么", or "最近做了什么". This skill is not a shortcut for a fixed phrase or a project-specific workflow; it defines how to reconstruct work from local evidence when the user does not spell out the method.
---

# Weekly Work Summary

## Positioning

Use this skill as a workspace-agnostic local-evidence reporting protocol, not as a prompt macro.

The value is to make weekly summaries consistent when the user gives a short or vague request. Always reconstruct the summary from available workspace evidence instead of relying on memory, conversation history, or a literal restatement of the user's wording.

## Scope

Apply this skill to whichever workspace, repository, or folder is active in the current task. If the target is ambiguous and cannot be inferred from context, ask for the target folder before collecting evidence.

Do not assume any project-specific structure, language, file naming scheme, or output directory. Treat `.taskflow` as one possible signal, not a required dependency.

Use China Shanghai time (`Asia/Shanghai`) as the fixed default time basis for "this week", "last week", "today", workdays, and date windows, regardless of the user's device timezone, unless the user explicitly requests a different timezone.

## Core Contract

1. Produce a concise human-readable status summary from local workspace activity.
2. Make the evidence path stable across sessions, folders, and projects.
3. Filter noisy filesystem churn before drawing conclusions.
4. Separate facts from inference when the evidence is indirect.
5. Do not edit project files for a summary request.
6. For ordinary weekly-summary requests, final output should be only the weekly summary itself; do not append project delivery-report sections such as changed files, tradeoffs, self-check questions, or unfinished work unless the user explicitly asks for an implementation report.
7. Create an Excel deliverable only when the user requests it or the selected output profile requires it. Use the explicitly configured destination `WEEKLY_SUMMARY_OUTPUT_DIR`; if it is unset, use a workspace-local `artifacts/` directory. Never assume a machine-specific network share.

## Execution Protocol

Before writing prose, record the target folder, Shanghai date window, evidence sources used, and noise filters. Treat Git/task records as stronger than modification times. If evidence conflicts, report the conflict instead of selecting the more convenient narrative. Keep the normal summary read-only; generating an Excel file is a separate explicit deliverable with its own path and verification.

## Evidence Order

1. Git history, if the target folder is inside a repository.
2. Task records, such as `.taskflow/**/progress.md`, `.taskflow/**/task_plan.md`, issue notes, changelogs, sprint notes, or other project-local status files.
3. Recently modified documents, source files, config files, data files, and scripts.
4. Generated outputs that indicate real work, such as reports, spreadsheets, PDFs, archives, exports, builds, logs, packages, screenshots, or official deliverables.
5. File modification times when stronger evidence is unavailable.

If git is unavailable, say so briefly and continue with task records and file activity. Do not invent commit-like certainty from modification times.

## Noise Policy

Ignore or collapse low-signal files unless they reveal an output event:

- Cache folders, bytecode, build intermediates, and temporary scratch files.
- Repeated static snapshots with the same purpose.
- Large batches of similar generated files; summarize them as one production event.
- Tool-local metadata unless it changes the work narrative.

## Inference Rules

- Prefer themes over file-by-file accounting.
- Do not force every file to map to a specific day.
- When the user asks for "one sentence per day", "每天一句", or an equivalent day-by-day format, first determine the concrete workdays in the requested week before writing the summary.
- For "this week", "last week", or any workday-based request, first identify the requested week's actual China workdays using the China Shanghai date window, accounting for weekends, Chinese public holidays, and make-up workdays. Verify against the official China holiday calendar when possible; if verification is not possible, state the basis used instead of silently assuming Monday through Friday.
- For a day-by-day retrospective, first summarize the whole week's work from the evidence, then distribute that weekly narrative across the identified workdays. The sentences do not need to correspond one-to-one with activity that happened on that exact date.
- Allocate exactly one sentence to every identified workday in chronological order, and do not add extra sentences under a workday.
- Do not use empty placeholders such as "no local evidence", "no clear activity", or "no project progress" in the day-by-day final output. If direct evidence is sparse for a workday, assign one of the week's real work themes to that day instead of producing a no-work sentence.
- Do not allocate sentences to future workdays unless the user explicitly asks for a planned-week view. If the current week is incomplete and the user asks what was done, cover only elapsed workdays or use the most recent completed workweek with evidence when that better matches the request; state the date range.
- For other output profiles, if a day has no clear activity, omit it or describe it in user-facing language.
- Use concrete dates when relative dates could be ambiguous.
- Mark inference with wording such as "based on file names and outputs" when needed.

## Output Profiles

Choose the lightest profile that satisfies the user:

- Default weekly-summary output, including vague requests such as "weekly report", "status update", "本周工作总结", "周报", or "总结本周": first identify the actual China workdays, then write one bullet per workday and exactly one sentence per bullet, distributing the week's summarized work across those days rather than requiring exact per-day evidence.
- "One sentence per day" / "每天一句": use the same default workday-by-workday format; do not add theme sections before or after it.
- Theme-grouped summary: use only when the user explicitly asks to group by theme, module, workstream, or category.
- Formal status report: add completed work, ongoing work, risks, and next steps only if explicitly requested.
- Evidence audit: include sources, commands, key files, facts/speculation sections, or derivation details only if the user asks how the summary was derived.
- When creating a document or spreadsheet deliverable, use a clean user-facing title such as "工作总结" or "工作总结_YYYYMMDD-YYYYMMDD"; do not include internal basis labels such as "（上海工作日口径）" in the content title or filename unless the user explicitly asks for that wording.
- Save an explicitly requested Excel deliverable under `WEEKLY_SUMMARY_OUTPUT_DIR`, where the date range is the summarized week in China Shanghai date terms. If the configured destination is unavailable, the target file already exists and should not be overwritten, or the file is locked, report the exact reason instead of silently choosing another path.

Final answers must be in the user's language. For Chinese requests, write the user-facing summary entirely in Chinese, except for literal file names, commands, dates, paths, and project-specific identifiers that should remain unchanged.

## Useful Commands

Use `rg` or `rg --files` first when searching names or text. Use parallel reads when gathering independent evidence.

```powershell
git log --since="YYYY-MM-DD 00:00" --date=iso --pretty=format:"%h`t%ad`t%s" --name-only
Get-ChildItem -Path .taskflow -Recurse -File -Include progress.md,task_plan.md
Get-ChildItem -Recurse -File | Where-Object { $_.LastWriteTime -ge [datetime]'YYYY-MM-DD' } | Sort-Object LastWriteTime
```
