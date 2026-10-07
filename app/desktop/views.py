"""View controllers for Forge Desktop Shell («Кузница»)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.api.client import ForgeApiClient


class BaseView(ttk.Frame):
    """Base class for Forge Desktop views."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self.client = client

    def refresh(self) -> None:
        """Called when the view is navigated to or updated."""
        pass


class HomeView(BaseView):
    """Main landing view: «Кузница» overview, status badges, and quick task dispatch."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, client, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        # Title Section
        title_frame = ttk.Frame(self)
        title_frame.pack(fill="x", padx=20, pady=(20, 10))

        lbl_title = ttk.Label(
            title_frame,
            text="FORGE — «Кузница»",
            font=("Segoe UI", 20, "bold"),
        )
        lbl_title.pack(anchor="w")

        lbl_sub = ttk.Label(
            title_frame,
            text="AI-Native Engineering Environment • Version 0.1.0",
            font=("Segoe UI", 10),
        )
        lbl_sub.pack(anchor="w", pady=(2, 10))

        # Status Cards Frame
        self.status_frame = ttk.LabelFrame(self, text=" System Status ", padding=15)
        self.status_frame.pack(fill="x", padx=20, pady=10)

        self.lbl_core_status = ttk.Label(
            self.status_frame,
            text="● Forge Core: Checking...",
            font=("Segoe UI", 11, "bold"),
        )
        self.lbl_core_status.pack(anchor="w", pady=2)

        self.lbl_api_status = ttk.Label(
            self.status_frame,
            text="● API Layer: Checking...",
            font=("Segoe UI", 11, "bold"),
        )
        self.lbl_api_status.pack(anchor="w", pady=2)

        self.lbl_provider_status = ttk.Label(
            self.status_frame,
            text="● Providers: Checking...",
            font=("Segoe UI", 11),
        )
        self.lbl_provider_status.pack(anchor="w", pady=2)

        # Quick Actions Frame
        action_frame = ttk.LabelFrame(self, text=" Quick Dispatch & Workspace ", padding=15)
        action_frame.pack(fill="both", expand=True, padx=20, pady=10)

        ttk.Label(action_frame, text="Task Prompt / Instruction:", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.txt_task = ttk.Entry(action_frame, font=("Segoe UI", 10))
        self.txt_task.pack(fill="x", pady=6)
        self.txt_task.insert(0, "Write a small utility helper in Python")

        btn_row = ttk.Frame(action_frame)
        btn_row.pack(fill="x", pady=6)

        self.btn_run = ttk.Button(btn_row, text="⚡ Run Task in Core", command=self._on_run_task)
        self.btn_run.pack(side="left", padx=(0, 10))

        self.btn_refresh = ttk.Button(btn_row, text="🔄 Refresh Status", command=self.refresh)
        self.btn_refresh.pack(side="left")

        # Output Log Box
        ttk.Label(action_frame, text="Execution Output:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 2))
        self.txt_output = tk.Text(action_frame, height=8, font=("Consolas", 9), wrap="word")
        self.txt_output.pack(fill="both", expand=True, pady=4)
        self.txt_output.insert("1.0", "Ready to execute tasks through Forge Core.")

    def refresh(self) -> None:
        status = self.client.get_system_status()
        if status.core == "connected":
            self.lbl_core_status.config(text="● Forge Core: CONNECTED", foreground="green")
        else:
            self.lbl_core_status.config(text="● Forge Core: DISCONNECTED", foreground="red")

        if status.api == "connected":
            self.lbl_api_status.config(text="● API: CONNECTED", foreground="green")
        else:
            self.lbl_api_status.config(text="● API: ERROR", foreground="red")

        self.lbl_provider_status.config(
            text=f"● Provider Layer: {status.provider_status} ({status.providers_ready}/{status.providers_total} Ready)"
        )

    def _on_run_task(self) -> None:
        prompt = self.txt_task.get().strip()
        if not prompt:
            return
        self.txt_output.delete("1.0", "end")
        self.txt_output.insert("1.0", f"Starting task in Forge Core: '{prompt}'...\n")
        self.update_idletasks()

        res = self.client.run_task(description=prompt)
        status_str = "SUCCESS" if res.success else "FAILED"
        self.txt_output.insert("end", f"\n[Result: {status_str}]\n")
        self.txt_output.insert("end", f"Run ID: {res.run_id}\n")
        self.txt_output.insert("end", f"Tokens: {res.tokens} | Cost: ${res.cost:.4f} | Duration: {res.duration_seconds:.2f}s\n")
        self.txt_output.insert("end", f"Provider: {res.provider_used} ({res.model_used})\n\n")
        self.txt_output.insert("end", f"Output:\n{res.output}\n")
        if res.error:
            self.txt_output.insert("end", f"\nError: {res.error}\n")


class ProjectsView(BaseView):
    """Projects & Workspace browser."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, client, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        title_frame = ttk.Frame(self)
        title_frame.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Label(title_frame, text="Projects & Workspaces", font=("Segoe UI", 18, "bold")).pack(anchor="w")

        self.tree = ttk.Treeview(self, columns=("id", "name", "path", "status"), show="headings")
        self.tree.heading("id", text="Project ID")
        self.tree.heading("name", text="Project Name")
        self.tree.heading("path", text="Workspace Path")
        self.tree.heading("status", text="Status")
        self.tree.pack(fill="both", expand=True, padx=20, pady=10)

    def refresh(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        projects = self.client.list_projects()
        for p in projects:
            self.tree.insert("", "end", values=(p.project_id, p.name, p.path, p.status))


class AgentsView(BaseView):
    """Agents & Model Capabilities matrix."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, client, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        title_frame = ttk.Frame(self)
        title_frame.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Label(title_frame, text="Agents & Models", font=("Segoe UI", 18, "bold")).pack(anchor="w")

        self.tree = ttk.Treeview(self, columns=("name", "provider", "model", "status", "caps"), show="headings")
        self.tree.heading("name", text="Agent Name")
        self.tree.heading("provider", text="Provider")
        self.tree.heading("model", text="Configured Model")
        self.tree.heading("status", text="Status")
        self.tree.heading("caps", text="Capabilities")
        self.tree.pack(fill="both", expand=True, padx=20, pady=10)

    def refresh(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        agents = self.client.list_agents()
        for a in agents:
            self.tree.insert("", "end", values=(a.name, a.provider, a.model, a.status, ", ".join(a.capabilities)))


class RunsView(BaseView):
    """Execution Runs and History."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, client, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        title_frame = ttk.Frame(self)
        title_frame.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Label(title_frame, text="Runs & Traceability", font=("Segoe UI", 18, "bold")).pack(anchor="w")

        self.tree = ttk.Treeview(self, columns=("run_id", "task", "provider", "tokens", "cost", "duration", "status"), show="headings")
        self.tree.heading("run_id", text="Run ID")
        self.tree.heading("task", text="Task ID")
        self.tree.heading("provider", text="Provider / Model")
        self.tree.heading("tokens", text="Tokens")
        self.tree.heading("cost", text="Cost")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("status", text="Status")
        self.tree.pack(fill="both", expand=True, padx=20, pady=10)

    def refresh(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        report = self.client.get_dashboard()
        for r in report.runs:
            status = "SUCCESS" if r.success else "FAILED"
            prov_mod = f"{r.provider} ({r.model})" if r.provider else "N/A"
            self.tree.insert(
                "",
                "end",
                values=(
                    r.run_id[:16],
                    r.task_id,
                    prov_mod,
                    f"{r.total_tokens:,}",
                    f"${r.effective_cost:.4f}",
                    f"{r.duration_seconds:.2f}s",
                    status,
                ),
            )


class CostsView(BaseView):
    """Live Provider & Cost Dashboard."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, client, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        header_frame = ttk.Frame(self)
        header_frame.pack(fill="x", padx=20, pady=(20, 10))

        ttk.Label(header_frame, text="Provider & Cost Dashboard", font=("Segoe UI", 18, "bold")).pack(side="left")

        btn_refresh = ttk.Button(header_frame, text="🔄 Refresh Accounts", command=self._on_refresh_accounts)
        btn_refresh.pack(side="right")

        # Summary Bar
        self.lbl_totals = ttk.Label(self, text="Total Spent: $0.00 | Total Runs: 0 | Total Tokens: 0", font=("Segoe UI", 10, "bold"))
        self.lbl_totals.pack(fill="x", padx=20, pady=5)

        # Table
        self.tree = ttk.Treeview(
            self,
            columns=("provider", "status", "balance", "spent", "requests", "runs", "tokens", "cost", "alert"),
            show="headings",
        )
        self.tree.heading("provider", text="Provider")
        self.tree.heading("status", text="Account Status")
        self.tree.heading("balance", text="Balance / Quota")
        self.tree.heading("spent", text="Forge Spent")
        self.tree.heading("requests", text="Requests")
        self.tree.heading("runs", text="Runs")
        self.tree.heading("tokens", text="Total Tokens")
        self.tree.heading("cost", text="Cost")
        self.tree.heading("alert", text="Alert")
        self.tree.pack(fill="both", expand=True, padx=20, pady=10)

    def refresh(self) -> None:
        report = self.client.get_dashboard(force_refresh=False)
        self._render_report(report)

    def _on_refresh_accounts(self) -> None:
        self.client.refresh_dashboard()
        report = self.client.get_dashboard(force_refresh=True)
        self._render_report(report)

    def _render_report(self, report: Any) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)

        tot = report.all_time_totals
        self.lbl_totals.config(
            text=f"Total Forge Spent: ${tot.total_spent:.4f} | Total Runs: {tot.total_runs} ({tot.successful_runs} ok, {tot.failed_runs} fail) | Total Tokens: {tot.total_tokens:,}"
        )

        for p in report.providers:
            spent_str = f"${p.forge_spent:.4f}" if p.forge_spent > 0 else "$0.00"
            cost_str = f"${p.effective_cost:.4f}" if p.effective_cost > 0 else "$0.00"
            self.tree.insert(
                "",
                "end",
                values=(
                    p.provider,
                    p.account_status.status.value,
                    p.account_status.balance_display,
                    spent_str,
                    p.total_requests,
                    p.total_runs,
                    f"{p.total_tokens:,}",
                    cost_str,
                    p.account_status.alert.value,
                ),
            )


class SettingsView(BaseView):
    """Settings, Configuration, and First-Run Wizard Entry Point."""

    def __init__(self, parent: Any, client: ForgeApiClient, **kwargs: Any) -> None:
        super().__init__(parent, client, **kwargs)
        self._build_ui()

    def _build_ui(self) -> None:
        title_frame = ttk.Frame(self)
        title_frame.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Label(title_frame, text="Settings & First-Run Setup", font=("Segoe UI", 18, "bold")).pack(anchor="w")

        # First-Run Wizard Placeholder Box
        wizard_frame = ttk.LabelFrame(self, text=" First-Run Setup Wizard ", padding=15)
        wizard_frame.pack(fill="x", padx=20, pady=10)

        ttk.Label(
            wizard_frame,
            text="Configure your AI Providers, GitHub integration, local Workspace, and Execution Tools.",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 10))

        btn_wizard = ttk.Button(wizard_frame, text="🚀 Launch Setup Wizard (Placeholder)", command=lambda: None)
        btn_wizard.pack(anchor="w")

        # Security Architecture Notice
        sec_frame = ttk.LabelFrame(self, text=" Security Perimeter Notice ", padding=15)
        sec_frame.pack(fill="x", padx=20, pady=10)

        notice_text = (
            "Forge Desktop operates under zero-trust UI security:\n"
            "• API keys and provider tokens are NEVER stored in UI state or sent to renderer.\n"
            "• Secrets are managed strictly by SecretStore in the Core perimeter.\n"
            "• All tool executions are governed by ExecutionBoundary and RunScope."
        )
        ttk.Label(sec_frame, text=notice_text, font=("Consolas", 9)).pack(anchor="w")
