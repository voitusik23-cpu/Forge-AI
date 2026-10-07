"""Forge Desktop Shell («Кузница») v0.1."""

from app.desktop.app import ForgeDesktopApp
from app.desktop.views import (
    AgentsView,
    BaseView,
    CostsView,
    HomeView,
    ProjectsView,
    RunsView,
    SettingsView,
)

__all__ = [
    "AgentsView",
    "BaseView",
    "CostsView",
    "ForgeDesktopApp",
    "HomeView",
    "ProjectsView",
    "RunsView",
    "SettingsView",
]
