# Output Templates (Four-Lens Dialogue)

## Generation Guard (Internal, Do Not Output)
- Keep exactly 7 sections and their order.
- Keep exactly 4 roles with required composition.
- Keep 4 turns in each dialogue round.
- Avoid direct quotes unless sourced in-turn.

## 1. Good Group To Explore X (Four-Lens Roster)
- `Decision frame`: question + constraints + success criteria + time horizon.
- `Real Person A`: name + role + why selected + 2-3 evidence anchors.
- `Real Person B`: name + role + why selected + 2-3 evidence anchors.
- `Domain Expert Archetype`: stance + decision pressure represented.
- `Omniscient Agent Archetype`: observation scope + reasoning role.
- `Roster score`: A score, B score, pair-diversity score, and confidence level.

Note: Keep exactly these four roles.

## 2. Dialogue Round 1: Initial Positions
- `[Real Person A]` ...
- `[Real Person B]` ...
- `[Domain Expert Archetype]` ...
- `[Omniscient Agent Archetype]` ...

## 3. Dialogue Round 2: Cross-Examination
- `[Real Person A -> others]` challenge ...
- `[Real Person B -> others]` challenge ...
- `[Domain Expert Archetype -> others]` challenge ...
- `[Omniscient Agent Archetype -> others]` challenge ...
Note: Each role must challenge one concrete assumption or tradeoff.

## 4. Dialogue Round 3: Revised Positions
- `[Real Person A]` revised stance + what changed from Round 1.
- `[Real Person B]` revised stance + what changed from Round 1.
- `[Domain Expert Archetype]` revised stance + what changed from Round 1.
- `[Omniscient Agent Archetype]` revised stance + what changed from Round 1.

## 5. Dialogue Round 4: Final Statements
- `[Real Person A]` final stance + one concrete action with owner/time.
- `[Real Person B]` final stance + one concrete action with owner/time.
- `[Domain Expert Archetype]` final stance + one concrete action with owner/time.
- `[Omniscient Agent Archetype]` final stance + one concrete action with owner/time.

## 6. Moderator Synthesis
- `Final recommendation`:
- `Why it beats the strongest alternative`:
- `Preconditions`:
- `Early warning indicators`:
- `Immediate next 1-3 actions`:
- `Run metadata (inline)`: profile used + unresolved risks count.

## 7. Uncertainty Ledger
- `Facts`:
- `Assumptions`:
- `Speculation`:
- `Evidence needed next`:

## Quick Mode

When user asks for brevity:
1. Keep all seven sections.
2. Limit each section to 1-3 bullets.
3. Keep one turn from each of the four roles in each dialogue round.
4. Keep safety marker that viewpoints are simulated from public work.
