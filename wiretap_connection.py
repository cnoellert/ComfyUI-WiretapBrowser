"""
Wiretap Connection Manager
Handles connections to Autodesk Flame Wiretap servers (IFFFS and Gateway)
and provides methods for browsing the node hierarchy.
"""

import os
import sys
import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ComfyUI-WiretapBrowser")

# ---------------------------------------------------------------------------
# Wiretap SDK import handling
# The Wiretap Python SDK ships with Flame and is typically located at:
#   /opt/Autodesk/wiretap/tools/current/python
#   /usr/discreet/wiretap/tools/current/python
# Users can also set WIRETAP_SDK_PATH environment variable.
# ---------------------------------------------------------------------------

import glob
import subprocess
import json as _json

# Static paths to check first
WIRETAP_SDK_PATHS = [
    os.environ.get("WIRETAP_SDK_PATH", ""),
    "/opt/Autodesk/wiretap/tools/current/python",
    "/usr/discreet/wiretap/tools/current/python",
    "/opt/Autodesk/wiretap/tools/current",
    "/usr/discreet/wiretap/tools/current",
]

# Dynamically discover Autodesk Python site-packages containing the adsk module.
# Flame installs its Python SDK at paths like:
#   /opt/Autodesk/python/<version>/lib/python3.11/site-packages/
# The .so lives under an `adsk` subdir there.
_dynamic_paths = sorted(
    glob.glob("/opt/Autodesk/python/*/lib/python*/site-packages"),
    reverse=True,  # newest version first
)
WIRETAP_SDK_PATHS.extend(_dynamic_paths)

# Also check the flamefamily python dirs
_dynamic_paths2 = sorted(
    glob.glob("/opt/Autodesk/.flamefamily_*/python"),
    reverse=True,
)
WIRETAP_SDK_PATHS.extend(_dynamic_paths2)

_wiretap_available = False
_wiretap_import_error = None

for sdk_path in WIRETAP_SDK_PATHS:
    if sdk_path and os.path.isdir(sdk_path) and sdk_path not in sys.path:
        sys.path.insert(0, sdk_path)

# ---------------------------------------------------------------------------
# Safe import: the Wiretap .so can crash (segfault) if dependent libraries
# are missing or incompatible. We first probe the import in a subprocess to
# make sure it's safe before loading it into the main ComfyUI process.
# ---------------------------------------------------------------------------

def _probe_wiretap_import() -> Tuple[bool, str]:
    """Test Wiretap import in an isolated subprocess. Returns (ok, message)."""
    probe_script = (
        "import sys\n"
        f"sys.path = {sys.path!r}\n"
        "try:\n"
        "    from adsk.libwiretapPythonClientAPI import WireTapClient\n"
        "    print('OK')\n"
        "except Exception as e:\n"
        "    print(f'FAIL:{e}')\n"
    )
    # Use the same Python interpreter
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe_script],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        output = result.stdout.strip()
        if output == "OK":
            return True, "probe passed"
        elif output.startswith("FAIL:"):
            return False, output[5:]
        else:
            # Crashed (segfault, abort, etc.)
            return False, f"probe crashed (rc={result.returncode}): {result.stderr.strip()[:200]}"
    except subprocess.TimeoutExpired:
        return False, "probe timed out"
    except Exception as e:
        return False, f"probe error: {e}"


# Run the probe
_probe_ok, _probe_msg = _probe_wiretap_import()

if _probe_ok:
    try:
        from adsk.libwiretapPythonClientAPI import (
            WireTapClient,
            WireTapClientInit,
            WireTapClientUninit,
            WireTapServerHandle,
            WireTapServerId,
            WireTapNodeHandle,
            WireTapStr,
            WireTapInt,
            WireTapClipFormat,
        )
        _wiretap_available = True
        import adsk.libwiretapPythonClientAPI as _wt_mod
        logger.info(f"Wiretap SDK loaded from: {getattr(_wt_mod, '__file__', 'unknown')}")
    except Exception as e:
        _wiretap_import_error = str(e)
        logger.warning(f"Wiretap SDK import failed after probe: {e}")
else:
    _wiretap_import_error = _probe_msg
    logger.warning(
        f"Wiretap SDK probe failed: {_probe_msg}. "
        f"Python {sys.version_info.major}.{sys.version_info.minor} | "
        f"Searched: {[p for p in WIRETAP_SDK_PATHS if p]}. "
        f"The browser will run in MOCK mode for development."
    )


def is_wiretap_available() -> bool:
    return _wiretap_available


def get_wiretap_import_error() -> Optional[str]:
    return _wiretap_import_error


# ---------------------------------------------------------------------------
# Node type classification
# ---------------------------------------------------------------------------

class NodeType(Enum):
    SERVER = "SERVER"
    VOLUME = "VOLUME"
    PROJECT = "PROJECT"
    WORKSPACE = "WORKSPACE"
    LIBRARY_LIST = "LIBRARY_LIST"
    LIBRARY = "LIBRARY"
    REEL = "REEL"
    REEL_GROUP = "REEL_GROUP"
    CLIP = "CLIP"
    HIRES = "HIRES"
    LOWRES = "LOWRES"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_string(cls, s: str) -> "NodeType":
        try:
            return cls(s.upper())
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_browsable(self) -> bool:
        """Can this node type have children to browse into?"""
        return self in (
            NodeType.SERVER, NodeType.VOLUME, NodeType.PROJECT,
            NodeType.WORKSPACE, NodeType.LIBRARY_LIST, NodeType.LIBRARY,
            NodeType.REEL, NodeType.REEL_GROUP, NodeType.CLIP,
        )

    @property
    def is_clip(self) -> bool:
        return self in (NodeType.CLIP, NodeType.HIRES, NodeType.LOWRES)

    @property
    def icon(self) -> str:
        """Return an icon hint for the frontend."""
        icons = {
            NodeType.SERVER: "server",
            NodeType.VOLUME: "database",
            NodeType.PROJECT: "folder-open",
            NodeType.WORKSPACE: "layout",
            NodeType.LIBRARY_LIST: "library",
            NodeType.LIBRARY: "book-open",
            NodeType.REEL: "film",
            NodeType.REEL_GROUP: "layers",
            NodeType.CLIP: "image",
            NodeType.HIRES: "maximize",
            NodeType.LOWRES: "minimize",
        }
        return icons.get(self, "file")


@dataclass
class WiretapNode:
    """Represents a node in the Wiretap hierarchy."""
    node_id: str
    display_name: str
    node_type: NodeType
    server_name: str
    has_children: bool = True
    num_frames: int = 0
    width: int = 0
    height: int = 0
    bit_depth: int = 0
    fps: float = 0.0
    scan_format: str = ""
    pixel_format: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "node_type": self.node_type.value,
            "server_name": self.server_name,
            "has_children": self.has_children,
            "icon": self.node_type.icon,
            "is_clip": self.node_type.is_clip,
        }
        if self.node_type.is_clip:
            d.update({
                "num_frames": self.num_frames,
                "width": self.width,
                "height": self.height,
                "bit_depth": self.bit_depth,
                "fps": self.fps,
                "scan_format": self.scan_format,
                "pixel_format": self.pixel_format,
            })
        return d


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------

class WiretapConnectionManager:
    """
    Manages connections to Wiretap servers.

    Wiretap uses a client-server model:
    - IFFFS server: Exposes the Flame project database (projects, libraries, reels, clips)
    - Gateway server: Exposes filesystem media and streams as raw RGB

    The IFFFS node hierarchy is:
        /projects
            /<project_name>
                /Workspace (WORKSPACE)
                /Shared Libraries (LIBRARY_LIST)
                    /<library_name> (LIBRARY)
                        /<reel_name> (REEL)
                            /<clip_name> (CLIP)
                                /hires (HIRES)
                                /lowres (LOWRES)
    """

    def __init__(self):
        self._initialized = False
        self._servers: Dict[str, Any] = {}  # server_id -> WireTapServerHandle

    def initialize(self):
        """Initialize the Wiretap client library."""
        if not _wiretap_available:
            logger.warning("Wiretap SDK not available, using mock mode")
            return

        if not self._initialized:
            WireTapClientInit()
            self._initialized = True
            logger.info("Wiretap client initialized")

    def shutdown(self):
        """Shut down the Wiretap client library."""
        if self._initialized and _wiretap_available:
            self._servers.clear()
            WireTapClientUninit()
            self._initialized = False
            logger.info("Wiretap client shut down")

    def _get_server_handle(self, hostname: str, server_type: str = "IFFFS"):
        """Get or create a server handle."""
        server_id_str = f"{hostname}:{server_type}"
        if server_id_str not in self._servers:
            if server_type == "Gateway":
                sid = WireTapServerId("Gateway", hostname)
            else:
                sid = WireTapServerId(hostname, server_type)
            self._servers[server_id_str] = WireTapServerHandle(sid)
            logger.info(f"Connected to Wiretap server: {server_id_str}")
        return self._servers[server_id_str]

    def get_children(
        self, hostname: str, node_id: str = "/", server_type: str = "IFFFS"
    ) -> List[WiretapNode]:
        """
        Get children of a node in the Wiretap hierarchy.

        Args:
            hostname: The Flame workstation hostname or IP.
            node_id: The Wiretap node path (e.g. "/projects" or a specific node ID).
            server_type: "IFFFS" for Flame database, "Gateway" for filesystem.

        Returns:
            List of WiretapNode objects representing children.
        """
        if not _wiretap_available:
            return self._get_mock_children(hostname, node_id, server_type)

        self.initialize()
        server = self._get_server_handle(hostname, server_type)

        parent_handle = WireTapNodeHandle(server, node_id)
        num_children = WireTapInt(0)
        if not parent_handle.getNumChildren(num_children):
            logger.error(
                f"Failed to get children count for {node_id}: "
                f"{parent_handle.lastError()}"
            )
            return []

        children = []
        for i in range(int(num_children)):
            child_handle = WireTapNodeHandle()
            child_name = WireTapStr()
            child_type = WireTapStr()

            if not parent_handle.getChild(i, child_handle):
                logger.warning(f"Failed to get child {i} of {node_id}")
                continue

            if not child_handle.getDisplayName(child_name):
                child_name = WireTapStr(f"<unnamed_{i}>")

            if not child_handle.getNodeTypeStr(child_type):
                child_type = WireTapStr("UNKNOWN")

            child_node_id = child_handle.getNodeId().id()

            node_type = NodeType.from_string(str(child_type))
            node = WiretapNode(
                node_id=str(child_node_id),
                display_name=str(child_name),
                node_type=node_type,
                server_name=hostname,
            )

            # If it's a clip, fetch format info
            if node_type.is_clip:
                self._populate_clip_info(child_handle, node)

            children.append(node)

        return children

    def _populate_clip_info(self, node_handle, node: WiretapNode):
        """Populate clip format information."""
        try:
            fmt = WireTapClipFormat()
            if node_handle.getClipFormat(fmt):
                node.width = fmt.width()
                node.height = fmt.height()
                node.bit_depth = fmt.bitsPerPixel() // max(fmt.numChannels(), 1)
                node.fps = fmt.frameRate()

                num_frames = WireTapInt(0)
                if node_handle.getNumFrames(num_frames):
                    node.num_frames = int(num_frames)

                scan = fmt.scanFormat()
                if scan == WireTapClipFormat.SCAN_FORMAT_PROGRESSIVE:
                    node.scan_format = "Progressive"
                elif scan == WireTapClipFormat.SCAN_FORMAT_FIELD_1:
                    node.scan_format = "Field 1"
                elif scan == WireTapClipFormat.SCAN_FORMAT_FIELD_2:
                    node.scan_format = "Field 2"

                node.pixel_format = "RGB"
                node.has_children = True  # clips can have hires/lowres sub-nodes
            else:
                logger.warning(
                    f"Could not get clip format for {node.node_id}: "
                    f"{node_handle.lastError()}"
                )
        except Exception as e:
            logger.error(f"Error getting clip info: {e}")

    def get_clip_format(
        self, hostname: str, node_id: str, server_type: str = "IFFFS"
    ) -> Optional[Dict[str, Any]]:
        """Get detailed clip format information."""
        if not _wiretap_available:
            return self._get_mock_clip_format(hostname, node_id)

        self.initialize()
        server = self._get_server_handle(hostname, server_type)
        node_handle = WireTapNodeHandle(server, node_id)

        fmt = WireTapClipFormat()
        if not node_handle.getClipFormat(fmt):
            logger.error(f"Failed to get clip format: {node_handle.lastError()}")
            return None

        num_frames = WireTapInt(0)
        node_handle.getNumFrames(num_frames)

        return {
            "width": fmt.width(),
            "height": fmt.height(),
            "bits_per_pixel": fmt.bitsPerPixel(),
            "num_channels": fmt.numChannels(),
            "bit_depth": fmt.bitsPerPixel() // max(fmt.numChannels(), 1),
            "frame_rate": fmt.frameRate(),
            "num_frames": int(num_frames),
            "frame_buffer_size": fmt.frameBufferSize(),
            "pixel_ratio": fmt.pixelRatio(),
        }

    def read_frame(
        self,
        hostname: str,
        node_id: str,
        frame_number: int,
        server_type: str = "IFFFS",
    ) -> Optional[Tuple[bytes, Dict[str, Any]]]:
        """
        Read a single frame from a clip.

        Returns:
            Tuple of (raw_frame_bytes, format_dict) or None on failure.
        """
        if not _wiretap_available:
            return self._get_mock_frame(hostname, node_id, frame_number)

        self.initialize()
        server = self._get_server_handle(hostname, server_type)
        node_handle = WireTapNodeHandle(server, node_id)

        fmt = WireTapClipFormat()
        if not node_handle.getClipFormat(fmt):
            logger.error(f"Failed to get clip format: {node_handle.lastError()}")
            return None

        buffer_size = fmt.frameBufferSize()
        # Wiretap Python API expects a string buffer pre-allocated to the right size
        buff = "\0" * buffer_size

        if not node_handle.readFrame(frame_number, buff, buffer_size):
            logger.error(
                f"Failed to read frame {frame_number}: {node_handle.lastError()}"
            )
            return None

        format_info = {
            "width": fmt.width(),
            "height": fmt.height(),
            "bits_per_pixel": fmt.bitsPerPixel(),
            "num_channels": fmt.numChannels(),
            "bit_depth": fmt.bitsPerPixel() // max(fmt.numChannels(), 1),
            "frame_buffer_size": buffer_size,
        }

        # Convert string buffer to bytes
        raw_bytes = buff.encode("latin-1") if isinstance(buff, str) else buff

        return (raw_bytes, format_info)

    # -----------------------------------------------------------------------
    # Mock mode for development without a Flame workstation
    # -----------------------------------------------------------------------

    def _get_mock_children(
        self, hostname: str, node_id: str, server_type: str
    ) -> List[WiretapNode]:
        """Return mock hierarchy data for development/testing."""
        mock_tree = {
            "/": [
                WiretapNode("/projects", "projects", NodeType.VOLUME, hostname),
            ],
            "/projects": [
                WiretapNode(
                    "/projects/MyProject", "MyProject",
                    NodeType.PROJECT, hostname
                ),
                WiretapNode(
                    "/projects/CommercialSpot", "CommercialSpot",
                    NodeType.PROJECT, hostname
                ),
            ],
            # ── MyProject ──────────────────────────────────────────────
            "/projects/MyProject": [
                WiretapNode(
                    "/projects/MyProject/workspace_001", "Workspace",
                    NodeType.WORKSPACE, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs", "Shared Libraries",
                    NodeType.LIBRARY_LIST, hostname
                ),
            ],
            "/projects/MyProject/shared_libs": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_001", "Comp Library",
                    NodeType.LIBRARY, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_002", "Source Library",
                    NodeType.LIBRARY, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_003", "AI Output",
                    NodeType.LIBRARY, hostname
                ),
            ],
            "/projects/MyProject/shared_libs/lib_001": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_001/reel_001",
                    "Hero Comp", NodeType.REEL, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_001/reel_002",
                    "BG Plates", NodeType.REEL, hostname
                ),
            ],
            "/projects/MyProject/shared_libs/lib_001/reel_001": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_001/reel_001/clip_001",
                    "beauty_v03", NodeType.CLIP, hostname,
                    num_frames=96, width=1920, height=1080, bit_depth=10, fps=24.0,
                    scan_format="Progressive",
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_001/reel_001/clip_002",
                    "fg_plate_001", NodeType.CLIP, hostname,
                    num_frames=48, width=3840, height=2160, bit_depth=16, fps=24.0,
                    scan_format="Progressive",
                ),
            ],
            "/projects/MyProject/shared_libs/lib_001/reel_002": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_001/reel_002/clip_003",
                    "bg_cityscape", NodeType.CLIP, hostname,
                    num_frames=120, width=3840, height=2160, bit_depth=16, fps=24.0,
                    scan_format="Progressive",
                ),
            ],
            "/projects/MyProject/shared_libs/lib_002": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_002/reel_003",
                    "Camera A", NodeType.REEL, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_002/reel_004",
                    "Camera B", NodeType.REEL, hostname
                ),
            ],
            "/projects/MyProject/shared_libs/lib_002/reel_003": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_002/reel_003/clip_004",
                    "A001_C003_0210KV", NodeType.CLIP, hostname,
                    num_frames=240, width=4096, height=2160, bit_depth=12, fps=24.0,
                    scan_format="Progressive",
                ),
            ],
            "/projects/MyProject/shared_libs/lib_002/reel_004": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_002/reel_004/clip_005",
                    "B001_C001_0210KV", NodeType.CLIP, hostname,
                    num_frames=180, width=4096, height=2160, bit_depth=12, fps=24.0,
                    scan_format="Progressive",
                ),
            ],
            # AI Output library — empty reels ready for write destinations
            "/projects/MyProject/shared_libs/lib_003": [
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_003/reel_005",
                    "Upscaled", NodeType.REEL, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_003/reel_006",
                    "Style Transfer", NodeType.REEL, hostname
                ),
                WiretapNode(
                    "/projects/MyProject/shared_libs/lib_003/reel_007",
                    "Inpainting", NodeType.REEL, hostname
                ),
            ],
            "/projects/MyProject/shared_libs/lib_003/reel_005": [],
            "/projects/MyProject/shared_libs/lib_003/reel_006": [],
            "/projects/MyProject/shared_libs/lib_003/reel_007": [],
            # ── CommercialSpot ─────────────────────────────────────────
            "/projects/CommercialSpot": [
                WiretapNode(
                    "/projects/CommercialSpot/workspace_002", "Workspace",
                    NodeType.WORKSPACE, hostname
                ),
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs", "Shared Libraries",
                    NodeType.LIBRARY_LIST, hostname
                ),
            ],
            "/projects/CommercialSpot/shared_libs": [
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_010", "Edit Library",
                    NodeType.LIBRARY, hostname
                ),
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_011", "VFX Deliveries",
                    NodeType.LIBRARY, hostname
                ),
            ],
            "/projects/CommercialSpot/shared_libs/lib_010": [
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_010/reel_010",
                    "30sec Cut", NodeType.REEL, hostname
                ),
            ],
            "/projects/CommercialSpot/shared_libs/lib_010/reel_010": [
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_010/reel_010/clip_010",
                    "spot_30s_v02", NodeType.CLIP, hostname,
                    num_frames=720, width=1920, height=1080, bit_depth=10, fps=24.0,
                    scan_format="Progressive",
                ),
            ],
            "/projects/CommercialSpot/shared_libs/lib_011": [
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_011/reel_011",
                    "Received", NodeType.REEL, hostname
                ),
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_011/reel_012",
                    "AI Processed", NodeType.REEL, hostname
                ),
            ],
            "/projects/CommercialSpot/shared_libs/lib_011/reel_011": [
                WiretapNode(
                    "/projects/CommercialSpot/shared_libs/lib_011/reel_011/clip_011",
                    "vfx_shot_010_v01", NodeType.CLIP, hostname,
                    num_frames=64, width=2048, height=1152, bit_depth=32, fps=24.0,
                    scan_format="Progressive",
                ),
            ],
            "/projects/CommercialSpot/shared_libs/lib_011/reel_012": [],
        }

        return mock_tree.get(node_id, [])

    def _get_mock_clip_format(
        self, hostname: str, node_id: str
    ) -> Optional[Dict[str, Any]]:
        return {
            "width": 1920,
            "height": 1080,
            "bits_per_pixel": 30,
            "num_channels": 3,
            "bit_depth": 10,
            "frame_rate": 24.0,
            "num_frames": 96,
            "frame_buffer_size": 1920 * 1080 * 4,  # 10-bit = 4 bytes/pixel
            "pixel_ratio": 1.0,
        }

    def _get_mock_frame(
        self, hostname: str, node_id: str, frame_number: int
    ) -> Optional[Tuple[bytes, Dict[str, Any]]]:
        """Return a synthetic test frame for development."""
        import struct

        w, h = 1920, 1080
        # Generate a simple gradient test pattern (8-bit RGB for simplicity)
        pixels = bytearray(w * h * 3)
        for y in range(h):
            for x in range(w):
                idx = (y * w + x) * 3
                pixels[idx] = int(x / w * 255) & 0xFF       # R
                pixels[idx + 1] = int(y / h * 255) & 0xFF   # G
                pixels[idx + 2] = (frame_number * 10) & 0xFF # B
        format_info = {
            "width": w,
            "height": h,
            "bits_per_pixel": 24,
            "num_channels": 3,
            "bit_depth": 8,
            "frame_buffer_size": len(pixels),
        }
        return (bytes(pixels), format_info)


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

_connection_manager: Optional[WiretapConnectionManager] = None


def get_connection_manager() -> WiretapConnectionManager:
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = WiretapConnectionManager()
    return _connection_manager
