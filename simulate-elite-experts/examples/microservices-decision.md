# Example: Should our startup adopt a microservices architecture?

> Profile: classic | Language: English

## 1. Good Group To Explore X (Four-Lens Roster)

### Real Person A: Martin Fowler
- **Why**: Coined key microservices patterns; author of *Patterns of Enterprise Application Architecture*.
- **Evidence anchors**: (1) "Microservices" blog post (2014); (2) "MonolithFirst" pattern; (3) Refactoring book.
- **Score**: Domain 2 · Traceability 2 · Time 2 = 6/6

### Real Person B: Kelsey Hightower
- **Why**: Leading voice on Kubernetes and cloud-native infrastructure; represents operational reality.
- **Evidence anchors**: (1) "Kubernetes the Hard Way"; (2) GKE advocacy at Google; (3) public talks on operational complexity.
- **Score**: Domain 2 · Traceability 2 · Time 2 = 6/6

**Pair diversity**: Architecture theory vs operational practice → 2/2

### Domain Expert Archetype: Startup CTO (Series A, 8-person eng team)
### Omniscient Agent Archetype: System Complexity Analyst

---

## 2. Dialogue Round 1: Initial Positions

**Martin Fowler**: Start with a well-structured monolith. Microservices are a premature optimization for a team of 8 — you don't yet know your domain boundaries. The "MonolithFirst" approach lets you discover service boundaries through real usage before paying the distributed systems tax. [confidence: high]

**Kelsey Hightower**: If you go microservices now, you'll spend 60% of your engineering time on infrastructure instead of product. I've seen teams of this size drown in service mesh configs and deployment pipelines. A monolith deployed in a single container on a managed platform gets you to market faster. [confidence: high]

**Startup CTO**: Our investors are asking about "scalability." I'm worried a monolith won't survive a 10x traffic spike if we hit product-market fit.

**System Complexity Analyst**: The question isn't monolith vs microservices — it's about where complexity lives. A monolith concentrates complexity in code structure. Microservices distribute it across network, deployment, and observability. For 8 engineers, concentrated complexity is cheaper to manage.

---

*(Rounds 2-4 omitted for brevity — full output would include Cross-Examination, Revised Positions, and Final Statements)*

---

## 6. Moderator Synthesis

**Recommendation**: Start with a modular monolith. Enforce module boundaries in code (separate packages/namespaces per domain), but deploy as a single unit.

**Strongest alternative**: Microservices from day one — rejected because operational overhead exceeds team capacity.

**Preconditions**: Revisit when team exceeds 20 engineers OR when a specific module needs independent scaling.

**Early warnings**: If deployment frequency drops below 1/week due to monolith coupling, begin extracting the most contentious module.

**Next actions**:
1. Define 3-4 domain modules with explicit public interfaces
2. Set up a single CI/CD pipeline with one deployable artifact
3. Add latency monitoring per module to identify future extraction candidates

## 7. Uncertainty Ledger

| Type | Content | Confidence | Evidence needed |
|------|---------|------------|-----------------|
| Fact | Team size is 8 engineers | Confirmed | — |
| Assumption | Team lacks dedicated DevOps/SRE | Medium | Verify team composition |
| Assumption | Traffic patterns are unpredictable pre-PMF | Medium | Review analytics if available |
| Speculation | Modular monolith can sustain 10x growth | Low | Load test after MVP |

### Post-Use Self-Check
1. Before reading this analysis, what was your initial leaning?
2. After reading, has your position changed? If so, which argument was most persuasive?
3. Which assumption in the Uncertainty Ledger concerns you most?
4. What is one piece of evidence you could gather in the next 48 hours to reduce uncertainty?
5. If you had to decide right now, what would you choose and why?
