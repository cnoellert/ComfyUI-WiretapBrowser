"""
ComfyUI-WiretapBrowser

Custom node pack for browsing and loading clips from Autodesk Flame
via the Wiretap SDK directly into ComfyUI workflows.

Nodes:
  - WiretapBrowser:      Visual tree browser for the Flame clip library
  - WiretapClipLoader:   Reads frame data into ComfyUI IMAGE tensors
  - WiretapFrameWriter:  Writes processed frames back to Flame
  - WiretapServerInfo:   Connection status and diagnostics

Requirements:
  - Autodesk Wiretap SDK (Python bindings)
  - Network access to a Flame workstation running the Wiretap server
  - OR: runs in mock mode for development without Flame
"""

import logging
import os

# Configure logging
logger = logging.getLogger("ComfyUI-WiretapBrowser")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[Wiretap] %(levelname)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Import node classes
from .wiretap_browser import WiretapBrowser, WiretapServerInfo
from .wiretap_loader import WiretapClipLoader, WiretapFrameWriter

# ---------------------------------------------------------------------------
# ComfyUI Registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "WiretapBrowser": WiretapBrowser,
    "WiretapClipLoader": WiretapClipLoader,
    "WiretapFrameWriter": WiretapFrameWriter,
    "WiretapServerInfo": WiretapServerInfo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WiretapBrowser": "🔥 Flame Wiretap Browser",
    "WiretapClipLoader": "🔥 Flame Clip Loader",
    "WiretapFrameWriter": "🔥 Flame Clip Writer",
    "WiretapServerInfo": "🔥 Flame Server Info",
}

# Tell ComfyUI where to find our frontend JS extensions
WEB_DIRECTORY = os.path.join(os.path.dirname(__file__), "js")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

logger.info("ComfyUI-WiretapBrowser loaded successfully")
