import asyncio
import json
import os
from typing import Any

import requests


def _provider_request(provider: str, api_key: str, prompt: str) -> str:
    if provider == "openai":
        response = requests.post(
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Explain only the supplied aggregate campaign measurements. "
                            "Never infer causation or invent data. Return concise plain text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=(5, 30),
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])
    if provider == "anthropic":
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
                "max_tokens": 400,
                "temperature": 0.2,
                "system": (
                    "Explain only supplied aggregate campaign measurements. "
                    "Never infer causation or invent data. Return concise plain text."
                ),
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=(5, 30),
        )
        response.raise_for_status()
        content = response.json().get("content", [])
        return "\n".join(
            str(item.get("text", "")) for item in content if item.get("type") == "text"
        )
    raise ValueError("LLM_PROVIDER must be 'openai' or 'anthropic'.")


def _deterministic_explanation(question: str, evidence: dict[str, Any]) -> str:
    summary = str(evidence.get("summary", "No anomaly evidence is selected."))
    drivers = evidence.get("drivers", [])
    if not isinstance(drivers, list):
        drivers = []
    if any(token in question.casefold() for token in ("why", "cause", "driver")):
        return (
            f"{summary} Measured evidence: "
            + ("; ".join(str(driver) for driver in drivers) or "no quantified drivers are available.")
            + " These are diagnostic signals, not proof of causality."
        )
    return (
        f"{summary} The observed metrics support investigating "
        + (", ".join(str(driver) for driver in drivers[:3]) or "the recent metric trend")
        + ". Any budget action remains subject to the listed inventory and spend guardrails."
    )


async def explain(
    question: str,
    evidence: dict[str, Any],
) -> dict[str, str]:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return {
            "answer": _deterministic_explanation(question, evidence),
            "provider": "deterministic",
            "status": "fallback_no_key",
        }
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    prompt = json.dumps(
        {"question": question, "aggregate_evidence": evidence},
        ensure_ascii=False,
        allow_nan=False,
    )
    try:
        answer = await asyncio.to_thread(_provider_request, provider, api_key, prompt)
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        return {
            "answer": _deterministic_explanation(question, evidence),
            "provider": "deterministic",
            "status": f"fallback_provider_error:{type(exc).__name__}",
        }
    return {"answer": answer, "provider": provider, "status": "completed"}
