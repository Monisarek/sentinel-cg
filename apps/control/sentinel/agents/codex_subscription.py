"""Explicit ChatGPT-subscription execution through the official Codex CLI."""

import subprocess

from apps.control.sentinel.agents.codex import CodexStructuredClient
from apps.control.sentinel.agents.contracts import ProviderBlocked, ProviderTransportError


class CodexSubscriptionClient(CodexStructuredClient):
    def __init__(self, role, budget, budget_role=None):
        super().__init__(role, model_id="gpt-6.1-sol")
        self.budget = budget
        self.budget_role = budget_role or role

    def complete(self, prompt, schema):
        self.diagnostics.append({"request": {"prompt": prompt, "schema": schema}})
        return super().complete(prompt, schema)

    def _run_cli(self, command, **options):
        if not self.executable:
            raise ProviderBlocked("Install the official Codex CLI and sign in with ChatGPT")
        # The CLI owns credentials. API billing overrides never enter this process.
        environment = options["env"]
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("CODEX_API_KEY", None)
        auth = subprocess.run([self.executable, "login", "status"], env=environment,
                              capture_output=True, text=True, timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if auth.returncode or "Logged in using ChatGPT" not in auth.stdout + auth.stderr:
            raise ProviderBlocked("Codex must be signed in with ChatGPT subscription access")
        self.budget.reserve(self.budget_role)
        for feature in ("unified_exec", "tool_search", "sleep_tool", "unbounded_connection_retries"):
            command[-1:-1] = ["--disable", feature]
        options["timeout"] = min(self.timeout_seconds, self.budget.remaining_seconds(self.budget_role))
        try:
            result = subprocess.run(command, **options,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise ProviderTransportError("Codex subscription request exceeded the experiment deadline") from None
        self.budget.remaining_seconds(self.budget_role)
        return result
