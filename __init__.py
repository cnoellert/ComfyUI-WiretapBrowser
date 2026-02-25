"""
ComfyUI-WiretapBrowser

Custom node pack for browsing and loading clips from Autodesk Flame
via the Wiretap SDK directly into ComfyUI workflows.

Nodes:
  - WiretapBrowser:        Visual tree browser for the Flame clip library
  - WiretapClipLoader:     Reads frame data into ComfyUI IMAGE tensors
  - WiretapFrameWriter:    Writes processed frames back to Flame
  - WiretapServerInfo:     Connection status and SDK diagnostics
  - WiretapOCIOTransform:  OCIO colour space transform
  - WiretapMetadata:       Clip metadata extraction

Requirements:
  - Autodesk Wiretap SDK (Python bindings + CLI tools)
    Download: https://aps.autodesk.com/developer/overview/wiretap
    No Flame license required on the ComfyUI machine.
  - Network access to a Flame workstation running the IFFFS server
  - OR: runs in mock mode for development without Flame

Setup (ComfyUI on same machine as Flame):
  SDK is auto-discovered from /opt/Autodesk/. No configuration needed.

Setup (ComfyUI on a separate machine):
  Install the standalone Wiretap SDK, then set environment variables:
    WIRETAP_SDK_PATH   — directory containing the `adsk` Python package
    WIRETAP_TOOLS_DIR  — directory containing CLI tools (wiretap_rw_frame, etc.)
    WIRETAP_LIB_DIR    — directory containing libwiretapClientAPI.dylib/.so

  Use the Server Info node to verify SDK detection and connectivity.
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
from .ocio_transform import WiretapOCIOTransform
from .wiretap_metadata import WiretapMetadata

# ---------------------------------------------------------------------------
# ComfyUI Registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "WiretapBrowser": WiretapBrowser,
    "WiretapClipLoader": WiretapClipLoader,
    "WiretapFrameWriter": WiretapFrameWriter,
    "WiretapServerInfo": WiretapServerInfo,
    "WiretapOCIOTransform": WiretapOCIOTransform,
    "WiretapMetadata": WiretapMetadata,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WiretapBrowser": "🔥 Flame Wiretap Browser",
    "WiretapClipLoader": "🔥 Flame Clip Loader",
    "WiretapFrameWriter": "🔥 Flame Clip Writer",
    "WiretapServerInfo": "🔥 Flame Server Info",
    "WiretapOCIOTransform": "🔥 Flame OCIO Transform",
    "WiretapMetadata": "🔥 Flame Clip Metadata",
}

# Tell ComfyUI where to find our frontend JS extensions
WEB_DIRECTORY = os.path.join(os.path.dirname(__file__), "js")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

logger.info("ComfyUI-WiretapBrowser loaded successfully")
