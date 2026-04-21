# Claude Prompt: Memory Promotion and Reliability

Implement the learning and evaluation layer without enabling unsafe self-modification.

Goals:
- Expand structured memory, reflection, lesson proposals, and reliability scoring.
- Build replay and evaluation workflows that improve trust before increasing autonomy.

Deliverables:
- Memory promotion workflow with evidence review, deduplication, expiry, and conflict handling.
- Evaluation suites for hallucination resistance, policy compliance, and partial failure recovery.
- Reliability dashboards or reports powered by audit traces.
- Documentation for how lessons are approved, rejected, and retired.

Constraints:
- Do not let the assistant rewrite its own core code or policies autonomously.
- Keep learning scoped to memory, prompts, retrieval, and routing.
- Preserve explicit human oversight for high-impact lessons.

