from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT = Path("USER_HOME/autodl-tmp/epidemic-openclaw")
STATE = PROJECT / "storage/openclaw/state"
CONFIG = STATE / "openclaw.json"
SOURCE_WORKSPACE = PROJECT / "storage/openclaw/source-discovery-workspace"
SOURCE_AGENT_DIR = STATE / "agents/source-discovery-agent/agent"
MODEL = "vllm/qwen3-8b-awq-local"


def main() -> None:
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    provider = os.environ.get("OPENCLAW_KEYFREE_PROVIDER", "duckduckgo")
    if provider not in {"duckduckgo", "parallel-free"}:
        raise ValueError("unsupported key-free provider")
    tools = value.setdefault("tools", {})
    tools["profile"] = "minimal"
    tools["alsoAllow"] = [
        "normalize_epidemic_query",
        "register_epidemic_query",
        "start_epidemic_retrieval",
        "get_epidemic_retrieval_status",
        "start_epidemic_source_discovery",
        "get_epidemic_source_discovery_status",
        "start_epidemic_daily_update",
        "get_epidemic_daily_update_status",
    ]
    tools["deny"] = [item for item in tools.get("deny", []) if item != "group:web"]
    web = tools.setdefault("web", {})
    web["search"] = {
        "enabled": True,
        "provider": provider,
        "maxResults": 10,
        "timeoutSeconds": 30,
        "cacheTtlMinutes": 15,
    }
    web["fetch"] = {
        "enabled": True,
        "maxChars": 50_000,
        "maxCharsCap": 100_000,
        "timeoutSeconds": 20,
        "cacheTtlMinutes": 15,
    }
    tools.setdefault("elevated", {})["enabled"] = False
    tools.setdefault("exec", {})["security"] = "deny"
    tools.setdefault("fs", {})["workspaceOnly"] = True

    plugins = value.setdefault("plugins", {}).setdefault("entries", {})
    duckduckgo = plugins.setdefault("duckduckgo", {})
    duckduckgo["enabled"] = True
    duckduckgo["config"] = {"webSearch": {"region": "wt-wt", "safeSearch": "strict"}}
    if provider == "parallel-free":
        plugins.setdefault("parallel", {})["enabled"] = True

    agents = value.setdefault("agents", {})
    rows = {item.get("id"): item for item in agents.setdefault("list", [])}
    main_agent = rows.get("main", {"id": "main"})
    main_agent.update(
        {
            "default": True,
            "workspace": str(PROJECT / "storage/openclaw/workspace"),
            "agentDir": str(STATE / "agents/main/agent"),
            "model": MODEL,
        }
    )
    source_agent = rows.get("source-discovery-agent", {"id": "source-discovery-agent"})
    source_agent.update(
        {
            "default": False,
            "name": "Source Discovery Agent",
            "workspace": str(SOURCE_WORKSPACE),
            "agentDir": str(SOURCE_AGENT_DIR),
            "model": MODEL,
            "skills": [],
            "subagents": {"allowAgents": []},
            "tools": {
                "profile": "full",
                "allow": ["web_search", "web_fetch", "submit_source_candidate"],
                "deny": [
                    "group:runtime",
                    "group:fs",
                    "group:sessions",
                    "group:memory",
                    "group:ui",
                    "group:automation",
                    "group:messaging",
                    "group:nodes",
                    "group:agents",
                    "group:media",
                    "x_search",
                    "gateway",
                    "cron",
                    "message",
                ],
                "elevated": {"enabled": False},
                "exec": {"security": "deny"},
                "fs": {"workspaceOnly": True},
                "loopDetection": {
                    "enabled": True,
                    "historySize": 12,
                    "warningThreshold": 3,
                    "criticalThreshold": 5,
                    "globalCircuitBreakerThreshold": 8,
                    "detectors": {"genericRepeat": True, "knownPollNoProgress": True, "pingPong": True},
                },
            },
        }
    )
    agents["list"] = [main_agent, source_agent]

    SOURCE_WORKSPACE.mkdir(parents=True, exist_ok=True, mode=0o700)
    SOURCE_AGENT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    instructions = PROJECT / "config/openclaw/source-discovery-agent/AGENTS.md"
    target = SOURCE_WORKSPACE / "AGENTS.md"
    target.write_text(instructions.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o600)
    temporary = CONFIG.with_suffix(".json.p1-7ar5-tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, CONFIG)


if __name__ == "__main__":
    main()
