from __future__ import annotations


class BaseAgent:
    name = "base_agent"

    def run(self, *args, **kwargs):
        return {"agent": self.name, "status": "ok"}
