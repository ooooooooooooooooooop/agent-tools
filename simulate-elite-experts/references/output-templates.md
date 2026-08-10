# Output Templates (Four-Lens Dialogue)

## Generation Guard (Internal, Do Not Output)
- Section and round counts must match the active profile.
- Role composition must match the active profile.
- Keep required turns in each dialogue round.
- Avoid direct quotes unless sourced in-turn.
- Include confidence tags for all real-person turns.
- Include uncertainty snapshots after each dialogue round.
- Include execution mode and context basis in Section 1.
- Include roster diversity score in Section 1.
- Append Post-Use Self-Check after the final section.

## 1. Good Group To Explore X (Four-Lens Roster)
- `Decision frame`: question + constraints + success criteria + time horizon.
- `Execution mode`: one-shot or interactive + why this mode applies.
- `Context basis`: local/current sources checked, or explicit statement that no grounded context was available.
- `Real Person A`: name + role + why selected + 2-3 evidence anchors.
- `Real Person B`: name + role + why selected + 2-3 evidence anchors.
- `Domain Expert Archetype`: stance + decision pressure represented.
- `Omniscient Agent Archetype`: observation scope + reasoning role.
- `Roster score`: A score, B score, pair-diversity score, and confidence level.
- `Roster diversity`: real-person pressure diversity + archetype coverage + system-risk coverage.

Note: Keep exactly these four roles.

## 2. Dialogue Round 1: Initial Positions
- `[Real Person A]` ... `[confidence: high/medium/low]`
- `[Real Person B]` ... `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` ...
- `[Omniscient Agent Archetype]` ...
- `Uncertainty snapshot`: key evidence bases tagged (fact/assumption/speculation).

## 3. Dialogue Round 2: Cross-Examination
- `[Real Person A -> others]` challenge ... `[confidence: high/medium/low]`
- `[Real Person B -> others]` challenge ... `[confidence: high/medium/low]`
- `[Domain Expert Archetype -> others]` challenge ...
- `[Omniscient Agent Archetype -> others]` challenge ...
Note: Each role must challenge one concrete assumption or tradeoff.
- `Uncertainty snapshot`: assumptions challenged + survived/fallen.

## 4. Dialogue Round 3: Revised Positions
- `[Real Person A]` revised stance + what changed from Round 1. `[confidence: high/medium/low]`
- `[Real Person B]` revised stance + what changed from Round 1. `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` revised stance + what changed from Round 1.
- `[Omniscient Agent Archetype]` revised stance + what changed from Round 1.
- `Uncertainty snapshot`: new assumptions introduced / old assumptions resolved.

## 5. Dialogue Round 4: Final Statements
- `[Real Person A]` final stance + one concrete action with owner/time. `[confidence: high/medium/low]`
- `[Real Person B]` final stance + one concrete action with owner/time. `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` final stance + one concrete action with owner/time.
- `[Omniscient Agent Archetype]` final stance + one concrete action with owner/time.

## 6. Moderator Synthesis
- `Final recommendation`:
- `Why it beats the strongest alternative`:
- `Preconditions`:
- `Early warning indicators`:
- `Immediate next 1-3 actions`:
- `Meta-review challenge`: strongest blind-review challenge that affected the synthesis, or "none material".
- `Run metadata (inline)`: profile used + unresolved risks count.

## 7. Uncertainty Ledger
- `Facts`:
- `Assumptions`:
- `Speculation`:
- `Evidence needed next`:

## Post-Use Self-Check (Appendix — all profiles)
1. Before reading this analysis, what was your initial leaning?
2. After reading, has your position changed? If so, which argument was most persuasive?
3. Which assumption in the Uncertainty Ledger concerns you most?
4. What is one piece of evidence you could gather in the next 48 hours to reduce uncertainty?
5. If you had to decide right now, what would you choose and why?

Note: This block is mandatory in all profiles. It appears after the last numbered section as a non-numbered appendix.

## Quick Mode

When user asks for brevity:
1. Keep all sections required by the active profile.
2. Limit each section to 1-3 bullets.
3. Keep one turn from each active role in each dialogue round.
4. Keep safety marker that viewpoints are simulated from public work.

## Micro Profile Template (5 Sections)

### 1. Good Group To Explore X (Two-Lens Roster)
- `Decision frame`: question + constraints + success criteria + time horizon.
- `Execution mode`: one-shot or interactive + why this mode applies.
- `Context basis`: local/current sources checked, or explicit statement that no grounded context was available.
- `Real Person A`: name + role + why selected + 2-3 evidence anchors.
- `Domain Expert Archetype`: stance + decision pressure represented.
- `Roster score`: A score, diversity score, and confidence level.

### 2. Dialogue Round 1: Initial Positions
- `[Real Person A]` ... `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` ...
- `Uncertainty snapshot`: key evidence bases tagged (fact/assumption/speculation).

### 3. Dialogue Round 2: Final Statements
- `[Real Person A]` final stance + one concrete action with owner/time. `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` final stance + one concrete action with owner/time.

### 4. Moderator Synthesis
- `Final recommendation`:
- `Why it beats the strongest alternative`:
- `Preconditions`:
- `Early warning indicators`:
- `Immediate next 1-3 actions`:
- `Run metadata (inline)`: profile used + unresolved risks count.

### 5. Uncertainty Ledger
- `Facts`:
- `Assumptions`:
- `Speculation`:
- `Evidence needed next`:

## Deep Profile Additional Rounds Template

### Dialogue Round 5: Stress Test
- `[Real Person A]` describes the scenario where their recommendation fails catastrophically. `[confidence: high/medium/low]`
- `[Real Person B]` describes the scenario where their recommendation fails catastrophically. `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` describes the scenario where their recommendation fails catastrophically.
- `[Omniscient Agent Archetype]` describes the scenario where their recommendation fails catastrophically.

### Dialogue Round 6: Contingency Planning
- `[Real Person A]` proposes a fallback plan triggered by Round 5 failure scenarios. `[confidence: high/medium/low]`
- `[Real Person B]` proposes a fallback plan triggered by Round 5 failure scenarios. `[confidence: high/medium/low]`
- `[Domain Expert Archetype]` proposes a fallback plan triggered by Round 5 failure scenarios.
- `[Omniscient Agent Archetype]` proposes a fallback plan triggered by Round 5 failure scenarios.
