from .applications import ApplicationCapability
from .base import CapabilityAdapter
from .browser import BrowserCapability
from .desktop import DesktopCapability
from .filesystem import FilesystemCapability

__all__ = [
    "ApplicationCapability",
    "BrowserCapability",
    "CapabilityAdapter",
    "DesktopCapability",
    "FilesystemCapability",
]
