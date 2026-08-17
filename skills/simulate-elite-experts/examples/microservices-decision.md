# Example: Should our startup adopt a microservices architecture?

> Profile: classic | Language: English
> Safety note: These are simulated viewpoints inferred from public work, not direct quotes or private claims.

## 1. Good Group To Explore X (Four-Lens Roster)

- `Decision frame`: An 8-engineer startup must decide whether to adopt microservices now or start with a modular monolith. Success means shipping quickly without creating operational complexity the team cannot maintain. Time horizon: next 6-12 months.
- `Execution mode`: one-shot, because the prompt asks for a complete example analysis rather than a collaborative roster-selection session.
- `Context basis`: No local repository or company architecture files were provided; evidence anchors are known public anchors rather than current-turn source verification.
- `Real Person A`: Martin Fowler, software architecture author and practitioner. Selected because his public work covers microservices, refactoring, and the "MonolithFirst" pattern. Evidence anchors: martinfowler.com microservices article; "MonolithFirst" essay; Refactoring book and catalog. Score: domain 2, traceability 2, time relevance 2 = 6/6.
- `Real Person B`: Kelsey Hightower, cloud infrastructure practitioner and Kubernetes educator. Selected because his public work represents operational reality, platform complexity, and cloud-native deployment discipline. Evidence anchors: Kubernetes the Hard Way; Google Cloud and Kubernetes talks; public commentary on platform operational costs. Score: domain 2, traceability 2, time relevance 2 = 6/6.
- `Domain Expert Archetype`: Startup CTO for a Series A company with an 8-person engineering team. This role represents investor pressure, product velocity, hiring constraints, and ownership clarity.
- `Omniscient Agent Archetype`: System Complexity Analyst. This role tracks where complexity moves across code, network boundaries, deployment, observability, incident response, and team coordination.
- `Roster score`: Fowler 6/6, Hightower 6/6, pair diversity 2/2. Roster confidence: high because both real people have direct public work related to architecture and operations.
- `Roster diversity`: real-person pressure diversity 2/2, archetype coverage 2/2, system-risk coverage 2/2 = 6/6.

## 2. Dialogue Round 1: Initial Positions

- `[Martin Fowler]` Start with a well-structured monolith and keep module boundaries explicit. The team does not yet know its stable domain boundaries, so extracting services now risks turning guesses into network contracts. [confidence: high]
- `[Kelsey Hightower]` Microservices will move scarce startup time from product work into deployment, observability, service ownership, and incident response. A small team should buy managed infrastructure where possible and keep the application shape simple. [confidence: medium]
- `[Startup CTO]` The concern is not theory; the board is asking whether the platform can survive growth. If a monolith slows feature teams or cannot isolate failures, we may pay later with a painful rewrite.
- `[System Complexity Analyst]` The real decision is where complexity should live. A monolith concentrates complexity inside code and tests; microservices distribute it into network calls, schemas, deployment order, and runtime coordination.
- `Uncertainty snapshot`: Fact: team size is 8 engineers. Assumption: domain boundaries are still changing. Assumption: there is no dedicated platform team. Speculation: a modular monolith can absorb near-term 10x traffic if the hot paths are known.

## 3. Dialogue Round 2: Cross-Examination

- `[Martin Fowler -> Startup CTO]` Scalability is not a reason to split everything early; first identify which parts need independent scaling and which are only organizationally fashionable. What evidence shows that service independence is the current bottleneck? [confidence: high]
- `[Kelsey Hightower -> Martin Fowler]` A modular monolith still needs discipline. If the team cannot enforce package boundaries, ownership, tests, and deployment hygiene in one codebase, it will not suddenly enforce them across ten services. [confidence: medium]
- `[Startup CTO -> others]` The monolith recommendation assumes one deployable artifact will not block parallel work. What happens when two product bets need incompatible release schedules or data models?
- `[System Complexity Analyst -> others]` Both sides are under-specifying the migration trigger. The decision should define measurable extraction conditions rather than treat monolith or microservices as an identity choice.
- `Uncertainty snapshot`: Challenged assumption: service boundaries are known enough to extract now; status: weakened. Challenged assumption: monolith discipline is easier than service discipline; status: survives only if module ownership and tests are enforced.

## 4. Dialogue Round 3: Revised Positions

- `[Martin Fowler]` Keep the initial recommendation, but make the monolith modular from day one with explicit internal APIs and tests that prevent accidental cross-module coupling. The change from Round 1 is that architecture fitness checks become a required part of the recommendation. [confidence: high]
- `[Kelsey Hightower]` I would accept one or two independently deployed services only if there is a concrete operational reason, such as a third-party integration worker or high-risk background processing. The change from Round 1 is allowing targeted extraction rather than rejecting all service boundaries. [confidence: medium]
- `[Startup CTO]` I can defend a modular monolith to investors if the plan includes clear scale triggers, observability, and a credible extraction path. The decision must look like staged architecture, not avoidance.
- `[System Complexity Analyst]` The revised architecture should be a single deployable core plus extraction-ready seams around volatile or high-load modules. Complexity stays centralized until a measured bottleneck justifies distributing it.
- `Uncertainty snapshot`: New assumption: module boundaries can be enforced in code review and CI. Resolved assumption: "microservices now" is not necessary for investor-facing scalability if scale triggers are documented.

## 5. Dialogue Round 4: Final Statements

- `[Martin Fowler]` Choose a modular monolith for the next 6 months. Owner: tech lead; by end of week 2, define module boundaries, public interfaces, and coupling checks for the first three domains. [confidence: high]
- `[Kelsey Hightower]` Do not create a service fleet without the operational base to run it. Owner: infrastructure lead or acting CTO; within 30 days, set up one deploy pipeline, basic metrics, structured logs, and rollback practice before adding services. [confidence: medium]
- `[Startup CTO]` Commit to a staged plan: monolith now, extraction later when triggers fire. Owner: CTO; review architecture monthly against team size, deployment frequency, incident count, and module-level load.
- `[System Complexity Analyst]` The decision should include exit criteria. Extract a service only when one module has independent scaling needs, independent release cadence, or risk isolation needs that exceed the coordination cost.

## 6. Moderator Synthesis

- `Final recommendation`: Start with a modular monolith, not a broad microservices architecture. Keep the codebase internally modular, deploy as one artifact, and define service-extraction triggers before the team needs them.
- `Why it beats the strongest alternative`: Microservices from day one offers cleaner theoretical ownership, but for an 8-engineer startup it introduces operational work before the product and domain boundaries are stable.
- `Preconditions`: The team must enforce module boundaries, maintain tests around internal interfaces, and instrument the system enough to identify future extraction candidates.
- `Early warning indicators`: Deployment frequency falls below weekly because of coupling; one module consumes disproportionate compute; incidents repeatedly require isolating a risky subsystem; two teams need incompatible release schedules.
- `Immediate next 1-3 actions`: 1. Define 3-4 domain modules and their public interfaces this week. 2. Add CI checks or tests that catch cross-module leakage within 30 days. 3. Create an architecture review trigger list for service extraction and revisit monthly.
- `Meta-review challenge`: The strongest blind-review challenge is that "modular monolith" can become a label without enforcement; the recommendation only works if boundary tests, ownership, and extraction triggers are made operational.
- `Run metadata (inline)`: profile classic; unresolved risks count 4.

## 7. Uncertainty Ledger

- `Facts`: Team size is 8 engineers. The decision is about monolith versus microservices for the next 6-12 months. The simulated real-person viewpoints are based on public work, not direct quotes.
- `Assumptions`: The team has no dedicated platform/SRE function. Domain boundaries are still changing. Near-term growth is uncertain. Current bottleneck is product velocity, not independent service scaling.
- `Speculation`: A modular monolith can handle the next 10x growth phase if hot paths are monitored and extracted only when needed. Investor concerns can be satisfied with staged architecture evidence rather than immediate service decomposition.
- `Evidence needed next`: Current traffic profile; expected growth scenarios; team ownership map; deployment frequency; incident history; load-test results for likely hot paths.

### Post-Use Self-Check
1. Before reading this analysis, what was your initial leaning?
2. After reading, has your position changed? If so, which argument was most persuasive?
3. Which assumption in the Uncertainty Ledger concerns you most?
4. What is one piece of evidence you could gather in the next 48 hours to reduce uncertainty?
5. If you had to decide right now, what would you choose and why?
