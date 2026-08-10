# First-Use Guide — Simulate Elite Experts

## What This Framework Does

This skill simulates how a carefully chosen group of domain experts would debate a decision. It doesn't give you "the answer" — it gives you a structured thinking scaffold that surfaces tradeoffs, assumptions, and blind spots you might miss on your own.

## When to Use It

Use this framework when you're facing a decision that is:
- **Complex**: multiple stakeholders, competing constraints, non-obvious tradeoffs.
- **Consequential**: hard or costly to reverse once made.
- **Uncertain**: significant unknowns about facts, outcomes, or preferences.

**Don't use it** for simple factual queries, single-correct-answer problems, or trivial choices.

## Your First Run: A Walkthrough

### Step 1: Pick a Question

Start with something real but not your highest-stakes decision. Good first-run examples:
- "Should our team adopt TypeScript or stay with JavaScript?"
- "Should I learn framework X or framework Y for my next project?"
- "Should we build this feature in-house or use an existing library?"

### Step 2: Choose a Profile

| Profile | Best For | Output Size |
|---------|----------|-------------|
| `micro` | Quick gut-check, 2 perspectives | Short |
| `lean` | Standard decision with compressed output | Medium |
| `classic` | Most decisions (default) | Full |
| `deep` | High-stakes, irreversible decisions | Extended |

**For your first run, use `classic`** (it's the default, you don't need to specify it).

### Step 2.5: Choose an Execution Mode

Use `one-shot` when you want a complete answer immediately. This is the default.

Use `interactive` when you want to approve or swap the expert roster before the dialogue starts.

### Step 3: Run the Prompt

Simply ask:
```
使用 simulate-elite-experts 分析：[your question here]
```
or in English:
```
Use simulate-elite-experts to analyze: [your question here]
```

### Step 4: Interact with the Output

In `interactive` mode, the framework will first present a **roster** of 4 experts. Before the dialogue begins, you'll be asked:
- "Do these roles and people look right for your question?"

This is your chance to swap in someone more relevant. In default `one-shot` mode, the framework completes the full analysis in one response.

### Step 5: Read Actively, Not Passively

As you read the 4 rounds of dialogue, pay attention to:
- **Round 2 (Cross-Examination)**: Which challenges surprised you? Those are your blind spots.
- **Confidence tags**: `[confidence: low]` on a real person's statement means the framework is extrapolating beyond their published work — treat that claim with extra skepticism.
- **Uncertainty snapshots**: These track which assumptions survived scrutiny and which didn't.
- **Roster diversity**: A weak diversity score means the panel may be too homogeneous.
- **Context basis**: This tells you whether the answer used project/current evidence or only general public anchors.

### Step 6: Complete the Self-Check

At the end, you'll see 5 reflection questions. Answer them honestly — especially:
- "Before reading this analysis, what was your initial leaning?"
- "After reading, has your position changed?"

If nothing changed, either your initial instinct was well-founded, or you weren't reading actively enough. Consider re-reading Round 2.

## Common Mistakes to Avoid

1. **Treating the output as "the answer"** — It's a thinking aid, not an oracle. Your judgment is still required.
2. **Skipping the Uncertainty Ledger** — This is where the framework is most honest about what it doesn't know. Read it carefully.
3. **Using it for trivial decisions** — The framework has cognitive overhead. Don't use a sledgehammer to hang a picture.
4. **Ignoring low-confidence tags** — When a real person's simulated viewpoint is marked `[confidence: low]`, the framework is speculating. Weigh accordingly.

## Next Steps After Your First Run

- Try `lean` profile for a faster decision.
- Try `micro` profile for a quick two-perspective check.
- Try `deep` profile when stakes are high — it adds stress-testing and contingency planning rounds.
- Try specifying real people: "Use simulate-elite-experts with Linus Torvalds and Kelsey Hightower to analyze..."
