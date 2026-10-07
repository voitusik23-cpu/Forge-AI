"""Main Desktop Application shell for Forge AI («Кузница»)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional, Type

from app.api.client import ForgeApiClient
from app.api.service import ForgeApiService
from app.desktop.views import (
    AgentsView,
    BaseView,
    CostsView,
    HomeView,
    ProjectsView,
    RunsView,
    SettingsView,
)

# Attempt to load customtkinter for modern dark theme styling if available
try:
    import customtkinter as ctk
    _USE_CTK = True
except ImportError:
    _USE_CTK = False


class ForgeDesktopApp:
    """Forge Desktop Shell: native desktop application window for «Кузница»."""

    def __init__(
        self,
        client: Optional[ForgeApiClient] = None,
        api_service: Optional[ForgeApiService] = None,
        headless: bool = False,
    ) -> None:
        self._headless = headless
        self._client = client or ForgeApiClient(api_service=api_service or ForgeApiService())
        self._root: Optional[Any] = None
        self._current_view_name: str = ""
        self._views: Dict[str, BaseView] = {}
        self._nav_buttons: Dict[str, Any] = {}
        self._is_running: bool = False

    @property
    def client(self) -> ForgeApiClient:
        return self._client

    @property
    def root(self) -> Optional[Any]:
        return self._root

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def current_view_name(self) -> str:
        return self._current_view_name

    def initialize(self) -> None:
        """Initialize native window and widget hierarchy."""
        if _USE_CTK and not self._headless:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("blue")
            self._root = ctk.CTk()
        else:
            self._root = tk.Tk()

        self._root.title("FORGE — «Кузница»")
        self._root.geometry("1020x680")
        self._root.minsize(800, 500)

        # Style configuration
        self._setup_styles()

        # Layout: Sidebar (Left) + Content Area (Right)
        self._build_sidebar()
        self._build_content_area()

        self._is_running = True

    def _setup_styles(self) -> None:
        style = ttk.Style(self._root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

    def _build_sidebar(self) -> None:
        """Construct the left navigation sidebar."""
        self.sidebar = ttk.Frame(self._root, width=200, padding=10)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Logo / Brand
        lbl_brand = ttk.Label(
            self.sidebar,
            text="FORGE",
            font=("Segoe UI", 16, "bold"),
        )
        lbl_brand.pack(anchor="w", padx=10, pady=(10, 2))

        lbl_sub = ttk.Label(
            self.sidebar,
            text="«Кузница»",
            font=("Segoe UI", 11, "italic"),
        )
        lbl_sub.pack(anchor="w", padx=10, pady=(0, 20))

        # Nav items
        nav_items = [
            ("forge", "🔨  Forge"),
            ("projects", "📁  Projects"),
            ("agents", "🤖  Agents"),
            ("runs", "⚡  Runs"),
            ("costs", "💰  Costs"),
            ("settings", "⚙️  Settings"),
        ]

        for key, text in nav_items:
            btn = ttk.Button(
                self.sidebar,
                text=text,
                command=lambda k=key: self.show_view(k),
            )
            btn.pack(fill="x", padx=5, pady=4)
            self._nav_buttons[key] = btn

        # Bottom Connection indicator
        self.lbl_sidebar_status = ttk.Label(
            self.sidebar,
            text="● Core: Ready",
            font=("Segoe UI", 9),
            foreground="green",
        )
        self.lbl_sidebar_status.pack(side="bottom", anchor="w", padx=10, pady=10)

    def _build_content_area(self) -> None:
        """Construct the dynamic content container."""
        self.content_container = ttk.Frame(self._root)
        self.content_container.pack(side="right", fill="both", expand=True)

        # Register Views
        view_classes: Dict[str, Type[BaseView]] = {
            "forge": HomeView,
            "projects": ProjectsView,
            "agents": AgentsView,
            "runs": RunsView,
            "costs": CostsView,
            "settings": SettingsView,
        }

        for name, cls in view_classes.items():
            view = cls(self.content_container, self._client)
            self._views[name] = view

    def connect_api(self) -> bool:
        """Verify API/Core connectivity and update status indicators."""
        health = self._client.check_health()
        is_connected = (health.core_status == "connected")
        if self.lbl_sidebar_status:
            if is_connected:
                self.lbl_sidebar_status.config(text="● Core: CONNECTED", foreground="green")
            else:
                self.lbl_sidebar_status.config(text="● Core: DISCONNECTED", foreground="red")
        return is_connected

    def show_view(self, view_name: str) -> None:
        """Switch current visible view and trigger refresh."""
        if view_name not in self._views:
            return

        for name, view in self._views.items():
            if name == view_name:
                view.pack(fill="both", expand=True)
                view.refresh()
            else:
                view.pack_forget()

        self._current_view_name = view_name

    def start(self) -> None:
        """Full startup lifecycle: initialize -> connect -> show_ui."""
        self.initialize()
        self.connect_api()
        self.show_view("forge")

        if not self._headless and self._root:
            self._root.mainloop()

    def shutdown(self) -> None:
        """Clean shutdown lifecycle."""
        self._is_running = False
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
