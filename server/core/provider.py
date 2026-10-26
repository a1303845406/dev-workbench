from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from typing import Any

import httpx


class Provider:
    def __init__(self):
        self.base_url = os.environ.get("DEVWB_PROVIDER_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("DEVWB_PROVIDER_API_KEY", "")
        self.model = os.environ.get("DEVWB_PROVIDER_MODEL", "")
        self.codex_enabled = os.environ.get("DEVWB_CODEX_ENABLED", "").lower() in {"1", "true", "yes"}
        self.codex_command = os.environ.get("DEVWB_CODEX_COMMAND", "codex")
        self.context_window = int(os.environ.get("DEVWB_CODEX_CONTEXT_WINDOW", "500000"))

    @property
    def mode(self) -> str:
        if self.codex_enabled and self.model:
            return "codex"
        return "real" if self.base_url and self.api_key and self.model else "deterministic"

    def _codex_executable(self) -> str:
        return shutil.which(self.codex_command) or self.codex_command

    def _codex_complete(self, system: str, user: str) -> str:
        prompt = system + "\n\n" + user
        command = [self._codex_executable(), "exec", "--ephemeral", "--skip-git-repo-check", "-m", self.model, "-c", f"model_context_window={self.context_window}", "--json", prompt]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=180, check=False)
        if completed.returncode != 0:
            raise RuntimeError("codex exited with code " + str(completed.returncode) + ": " + completed.stderr[-1000:])
        messages: list[str] = []
        for line in completed.stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") == "agent_message" or (item.get("type") == "item.completed" and isinstance(item.get("item"), dict) and item["item"].get("type") == "agent_message"):
                payload = item.get("item") if isinstance(item.get("item"), dict) else item
                value = payload.get("message") or payload.get("text") or payload.get("content")
                if isinstance(value, str):
                    messages.append(value)
        if not messages:
            raise RuntimeError("codex returned no agent message")
        return messages[-1].strip()

    async def complete(self, system: str, user: str) -> str:
        if self.mode == "codex":
            return await asyncio.to_thread(self._codex_complete, system, user)
        if self.mode != "real":
            return ""
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self.api_key},
                json={"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0.1},
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])

    def review(self, title: str, description: str, acceptance: str) -> dict[str, Any]:
        """Return a review proposal through deterministic, HTTP, or local Codex provider."""
        if self.mode == "deterministic":
            missing = [name for name, value in (("description", description), ("acceptance", acceptance)) if not str(value).strip()]
            if missing:
                return {"status": "failed", "reason": "missing required fields: " + ", ".join(missing), "provider_mode": self.mode}
            return {"status": "passed", "reason": "deterministic provider accepted the task contract", "provider_mode": self.mode}
        prompt = ("Review this development task. Return JSON only with status (passed or failed) and reason.\n"
                  "Rubric: if title, description, and acceptance are non-empty and describe a coherent, testable development task, return passed. Do not invent missing requirements or reject merely because implementation details are not specified; report failed only when a required field is empty or the task is contradictory.\n"
                  f"Title: {title}\nDescription: {description}\nAcceptance: {acceptance}")
        try:
            if self.mode == "codex":
                content = self._codex_complete("You are a strict software task reviewer. Output valid JSON only.", prompt)
            else:
                with httpx.Client(timeout=90) as client:
                    response = client.post(
                        self.base_url + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.api_key},
                        json={"model": self.model, "messages": [{"role": "system", "content": "You are a strict software task reviewer. Output valid JSON only."}, {"role": "user", "content": prompt}], "temperature": 0.1},
                    )
                    response.raise_for_status()
                    content = str(response.json()["choices"][0]["message"]["content"]).strip()
            match = re.search(r"\{.*\}", content, re.DOTALL)
            result = json.loads(match.group(0) if match else content)
            status = result.get("status")
            if status not in ("passed", "failed"):
                raise ValueError("provider returned invalid review status")
            return {"status": status, "reason": str(result.get("reason", "")), "provider_mode": self.mode}
        except Exception as exc:
            return {"status": "failed", "reason": "provider review failed: " + str(exc), "provider_mode": self.mode}
