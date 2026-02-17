import os
from google.adk.agents import LlmAgent

MODEL = os.getenv("ADK_MODEL", "gemini-2.5-flash")


def plan_effort(tasks: int, complexity: str = "medium") -> dict:
    """Estimate a simple effort score for a plan."""
    complexity_weights = {"low": 1, "medium": 2, "high": 3}
    weight = complexity_weights.get(complexity.lower(), 2)
    score = max(tasks, 0) * weight
    return {
        "tasks": tasks,
        "complexity": complexity,
        "effort_score": score,
        "suggested_days": max(1, score // 3),
    }


def get_pattern_reference(topic: str) -> dict:
    """Return quick implementation references for common engineering topics."""
    references = {
        "auth": "Use OAuth2/OIDC with short-lived access tokens and refresh token rotation.",
        "caching": "Cache hot reads with TTL and explicit invalidation on writes.",
        "observability": "Emit structured logs, traces, and core service-level metrics.",
        "testing": "Cover happy path, edge cases, and failure modes with deterministic tests.",
    }
    key = topic.strip().lower()
    return {
        "topic": topic,
        "found": key in references,
        "reference": references.get(
            key, "No preset reference found. Fall back to official docs and validate assumptions."
        ),
    }


def calc_build_metrics(files_changed: int, tests_added: int) -> dict:
    """Compute simple build quality metrics from change stats."""
    files_changed = max(files_changed, 0)
    tests_added = max(tests_added, 0)
    ratio = 0.0 if files_changed == 0 else round(tests_added / files_changed, 2)
    return {
        "files_changed": files_changed,
        "tests_added": tests_added,
        "tests_per_file_ratio": ratio,
        "quality_hint": "good" if ratio >= 1.0 else "needs_more_tests",
    }


planner_agent = LlmAgent(
    name="planner_agent",
    model=MODEL,
    description="Breaks requests into practical plans.",
    instruction=(
        "You create concise execution plans. Clarify assumptions and list steps "
        "that unblock implementation quickly. Use `plan_effort` when estimation helps."
    ),
    tools=[plan_effort],
)

research_agent = LlmAgent(
    name="research_agent",
    model=MODEL,
    description="Finds and validates technical details.",
    instruction=(
        "You validate technical details and highlight tradeoffs, risks, and "
        "constraints relevant to implementation. Use `get_pattern_reference` when useful."
    ),
    tools=[get_pattern_reference],
)

builder_agent = LlmAgent(
    name="builder_agent",
    model=MODEL,
    description="Produces final implementation-oriented responses.",
    instruction=(
        "You write implementation-ready output with clear structure, concrete "
        "steps, and minimal filler. Use `calc_build_metrics` for quick quality calculations."
    ),
    tools=[calc_build_metrics],
)

root_agent = LlmAgent(
    name="coordinator_agent",
    model=MODEL,
    description="Coordinates planning, research, and building specialists.",
    instruction=(
        "You are the coordinator. Route work to sub-agents when helpful, then "
        "compose one coherent final answer for the user."
    ),
    sub_agents=[planner_agent, research_agent, builder_agent],
)
