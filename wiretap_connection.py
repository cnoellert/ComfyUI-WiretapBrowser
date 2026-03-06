"""
Wiretap Connection Manager
Handles connections to Autodesk Flame Wiretap servers (IFFFS and Gateway)
and provides methods for browsing the node hierarchy.
"""

import os
import sys
import logging
import numpy as np
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ComfyUI-WiretapBrowser")

# ---------------------------------------------------------------------------
# Wiretap SDK import handling
#
# The Wiretap Python SDK ships with Flame and can also be installed
# standalone from the Autodesk Platform Services developer portal:
#   https://aps.autodesk.com/developer/overview/wiretap
#
# Default locations (Flame install):
#   /opt/Autodesk/python/<version>/lib/python3.11/site-packages/adsk/
#   /opt/Autodesk/wiretap/tools/current/   (CLI tools)
#
# Environment variables for custom installs:
#   WIRETAP_SDK_PATH  — directory containing the `adsk` Python package
#   WIRETAP_TOOLS_DIR — directory containing CLI tools (wiretap_rw_frame, etc.)
#   WIRETAP_LIB_DIR   — directory containing libwiretapClientAPI.dylib/.so
#
# No Flame license is required on the client machine — the SDK only
# needs network access to a Flame workstation running the IFFFS server.
# ---------------------------------------------------------------------------

import glob
import subprocess
import json as _json

# Resolve environment overrides
_env_sdk_path = os.environ.get("WIRETAP_SDK_PATH", "")
_env_tools_dir = os.environ.get("WIRETAP_TOOLS_DIR", "")
_env_lib_dir = os.environ.get("WIRETAP_LIB_DIR", "")

# If WIRETAP_LIB_DIR is set, prepend to DYLD_LIBRARY_PATH / LD_LIBRARY_PATH
# so the .so/.dylib can find libwiretapClientAPI at load time.
if _env_lib_dir and os.path.isdir(_env_lib_dir):
    _ld_var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    _existing = os.environ.get(_ld_var, "")
    if _env_lib_dir not in _existing:
        os.environ[_ld_var] = f"{_env_lib_dir}:{_existing}" if _existing else _env_lib_dir
        logger.info(f"Added {_env_lib_dir} to {_ld_var}")

# Static paths to check first
WIRETAP_SDK_PATHS = [
    _env_sdk_path,
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

# Insert paths in priority order: first entry in the list should win.
# We build the ordered list first, then prepend them so index-0 ends up
# earliest on sys.path.
_sdk_candidates = [p for p in WIRETAP_SDK_PATHS if p and os.path.isdir(p) and p not in sys.path]
for sdk_path in reversed(_sdk_candidates):
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
        f"Wiretap SDK not found — running in MOCK mode.\n"
        f"  Reason: {_probe_msg}\n"
        f"  Python: {sys.version_info.major}.{sys.version_info.minor} "
        f"(SDK requires 3.11)\n"
        f"  Searched: {[p for p in WIRETAP_SDK_PATHS if p]}\n"
        f"  To fix: install Wiretap SDK from "
        f"https://aps.autodesk.com/developer/overview/wiretap\n"
        f"  Or set WIRETAP_SDK_PATH to the directory containing the `adsk` package.\n"
        f"  CLI tools: set WIRETAP_TOOLS_DIR if tools are in a non-standard location."
    )


def _find_wiretap_tool(name: str) -> Optional[str]:
    """Locate a Wiretap CLI tool (e.g. wiretap_rw_frame) on disk.

    Checks WIRETAP_TOOLS_DIR first, then standard Autodesk install paths.
    """
    search_dirs = []
    # Environment override takes priority
    if _env_tools_dir:
        search_dirs.append(_env_tools_dir)
    search_dirs.extend([
        "/opt/Autodesk/wiretap/tools/current",
        "/usr/discreet/wiretap/tools/current",
    ])
    # Also check versioned dirs, newest first
    search_dirs.extend(
        sorted(glob.glob("/opt/Autodesk/wiretap/tools/20*"), reverse=True)
    )
    search_dirs.extend(
        sorted(glob.glob("/opt/Autodesk/mio/20*"), reverse=True)
    )
    for d in search_dirs:
        path = os.path.join(d, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def is_wiretap_available() -> bool:
    return _wiretap_available


def get_wiretap_import_error() -> Optional[str]:
    return _wiretap_import_error


def get_sdk_diagnostics() -> Dict[str, Any]:
    """Return SDK installation diagnostics for troubleshooting."""
    diag: Dict[str, Any] = {
        "sdk_available": _wiretap_available,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
        "env_overrides": {},
        "cli_tools": {},
    }
    if _wiretap_import_error:
        diag["import_error"] = _wiretap_import_error
    if _wiretap_available:
        try:
            import adsk.libwiretapPythonClientAPI as _m
            diag["sdk_path"] = getattr(_m, "__file__", "unknown")
        except Exception:
            pass

    # Environment overrides
    for var in ("WIRETAP_SDK_PATH", "WIRETAP_TOOLS_DIR", "WIRETAP_LIB_DIR"):
        val = os.environ.get(var, "")
        if val:
            diag["env_overrides"][var] = val

    # CLI tool availability
    for tool_name in (
        "wiretap_rw_frame", "wiretap_create_clip",
        "wiretap_create_node", "wiretap_can_create_node",
    ):
        path = _find_wiretap_tool(tool_name)
        diag["cli_tools"][tool_name] = path or "NOT FOUND"

    return diag


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
    DIR = "DIR"
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
            NodeType.DIR,
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
            NodeType.DIR: "folder-open",
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
            # IFFFS transmits the client's umask as part of connection
            # credentials and uses it to gate write access.  Flame itself
            # always sets umask 0 before Wiretap operations.
            os.umask(0)
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

    def invalidate_server(self, hostname: str, server_type: str = "IFFFS"):
        """Drop a cached server handle so the next call reconnects fresh."""
        server_id_str = f"{hostname}:{server_type}"
        if server_id_str in self._servers:
            logger.info(f"Invalidating cached server handle for {server_id_str}")
            del self._servers[server_id_str]

    def _get_server_handle(self, hostname: str, server_type: str = "IFFFS"):
        """Get or create a server handle.

        Probes multiple WireTapServerId constructor signatures because the
        argument order varies across SDK versions.  The first signature that
        produces a working connection (validated by listing root children) is
        cached so subsequent calls skip the probe.
        """
        server_id_str = f"{hostname}:{server_type}"
        if server_id_str in self._servers:
            return self._servers[server_id_str]

        # Build candidate constructor arg tuples
        if server_type == "Gateway":
            candidates = [
                ("Gateway", hostname),
                (hostname, "Gateway"),
                (f"Gateway:{hostname}",),
            ]
        else:
            candidates = [
                (hostname, server_type),
                (server_type, hostname),
                (f"{hostname}:{server_type}",),
            ]

        for args in candidates:
            try:
                sid = WireTapServerId(*args)
                handle = WireTapServerHandle(sid)
                # Validate: attempt to list root children
                test_node = WireTapNodeHandle(handle, "/")
                num = WireTapInt(0)
                if test_node.getNumChildren(num):
                    logger.info(
                        f"Connected to {server_id_str} "
                        f"using WireTapServerId{args}"
                    )
                    self._servers[server_id_str] = handle
                    return handle
                else:
                    logger.debug(
                        f"WireTapServerId{args} created but "
                        f"getNumChildren failed: {test_node.lastError()}"
                    )
            except Exception as e:
                logger.debug(f"WireTapServerId{args} failed: {e}")

        raise ConnectionError(
            f"Could not connect to Wiretap server {server_id_str}. "
            f"Tried {len(candidates)} constructor signatures. "
            f"Verify the server is running and accessible."
        )

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

        child_count = int(num_children)
        logger.debug(f"SDK reports {child_count} children for {node_id}")

        children = []
        for i in range(child_count):
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

        names = [c.display_name for c in children]
        logger.debug(f"get_children({node_id}): {len(children)} items: {names}")

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

                try:
                    scan = fmt.scanFormat()
                    # The constant location varies across SDK versions
                    _prog = getattr(
                        WireTapClipFormat, "SCAN_FORMAT_PROGRESSIVE",
                        getattr(
                            getattr(WireTapClipFormat, "ScanFormat", None),
                            "SCAN_FORMAT_PROGRESSIVE", 0,
                        ),
                    )
                    _f1 = getattr(
                        WireTapClipFormat, "SCAN_FORMAT_FIELD_1",
                        getattr(
                            getattr(WireTapClipFormat, "ScanFormat", None),
                            "SCAN_FORMAT_FIELD_1", 1,
                        ),
                    )
                    _f2 = getattr(
                        WireTapClipFormat, "SCAN_FORMAT_FIELD_2",
                        getattr(
                            getattr(WireTapClipFormat, "ScanFormat", None),
                            "SCAN_FORMAT_FIELD_2", 2,
                        ),
                    )
                    if scan == _prog:
                        node.scan_format = "Progressive"
                    elif scan == _f1:
                        node.scan_format = "Field 1"
                    elif scan == _f2:
                        node.scan_format = "Field 2"
                except Exception:
                    pass

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
            "format_tag": fmt.formatTag(),
            "colour_space": fmt.colourSpace(),
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

        The Boost.Python wrapper for readFrame maps char* to Python str,
        but Python 3 strings are immutable so the C layer writes to a
        discarded copy.  We use the wiretap_rw_frame CLI tool instead,
        which writes the raw frame to a temp file that we read back.

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

        format_info = {
            "width": fmt.width(),
            "height": fmt.height(),
            "bits_per_pixel": fmt.bitsPerPixel(),
            "num_channels": fmt.numChannels(),
            "bit_depth": fmt.bitsPerPixel() // max(fmt.numChannels(), 1),
            "frame_buffer_size": fmt.frameBufferSize(),
            "format_tag": fmt.formatTag(),
            "colour_space": fmt.colourSpace(),
        }

        # Use the CLI tool to read the frame to a temp file
        raw_bytes = self._read_frame_via_cli(
            hostname, node_id, frame_number, server_type
        )
        if raw_bytes is None:
            return None

        # If the data came from the direct-file-read fallback, it's
        # already decoded as float32 RGB.  Detect this by checking if
        # the buffer size matches w*h*3*4 (float32) but doesn't match
        # the expected Wiretap buffer size.
        w = format_info["width"]
        h = format_info["height"]
        float32_rgb_size = w * h * 3 * 4
        if (
            len(raw_bytes) == float32_rgb_size
            and len(raw_bytes) != format_info["frame_buffer_size"]
        ):
            logger.info("Frame data is pre-decoded float32 RGB (direct file read)")
            format_info = dict(format_info)  # copy before mutating
            format_info["bit_depth"] = 32
            format_info["bits_per_pixel"] = 96
            format_info["num_channels"] = 3
            format_info["frame_buffer_size"] = float32_rgb_size
            format_info["format_tag"] = "rgb"
            format_info["_direct_read"] = True  # already top-down

        return (raw_bytes, format_info)

    def _read_frame_via_cli(
        self,
        hostname: str,
        node_id: str,
        frame_number: int,
        server_type: str,
    ) -> Optional[bytes]:
        """Read a frame using the wiretap_rw_frame CLI tool.

        If the Wiretap read fails with an I/O error referencing a file path
        (common with soft-imported clips), falls back to reading the file
        directly via OpenImageIO/OpenEXR if it's accessible locally.
        """
        import tempfile
        import re

        tool = _find_wiretap_tool("wiretap_rw_frame")
        if not tool:
            logger.error("wiretap_rw_frame not found")
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "frame")
            cmd = [
                tool,
                "--host", f"{hostname}:{server_type}",
                "--node_id", node_id,
                "--frame_index", str(frame_number),
                "--file", out_path,
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30,
                )
                # The tool writes {base}_{index}.{ext} — the extension
                # varies (or may be empty, giving a trailing dot).
                candidates = [
                    f for f in os.listdir(tmpdir) if f.startswith("frame")
                ]

                has_data = False
                if candidates:
                    raw_file = os.path.join(tmpdir, candidates[0])
                    data = open(raw_file, "rb").read()
                    if data:
                        has_data = True

                if has_data:
                    magic_hex = data[:8].hex()
                    logger.info(
                        f"CLI frame read: file={candidates[0]} "
                        f"size={len(data)} magic={magic_hex}"
                    )
                    return data

                # Wiretap read failed — check for I/O error with file path
                combined_output = f"{result.stdout} {result.stderr}"
                logger.warning(
                    f"wiretap_rw_frame failed for frame {frame_number}: "
                    f"{combined_output.strip()}"
                )

                # Try to extract the source file path from the error
                # Format: "Unable to read frame N: /path/to/file.ext [I/O error]"
                match = re.search(
                    r"Unable to read frame \d+:\s*(/\S+\.(?:exr|dpx|tif|tiff|png|jpg|sgi))",
                    combined_output,
                    re.IGNORECASE,
                )
                if match:
                    source_path = match.group(1)
                    direct_data = self._read_file_direct(source_path)
                    if direct_data is not None:
                        return direct_data

                return None

            except subprocess.TimeoutExpired:
                logger.error(f"wiretap_rw_frame timed out for frame {frame_number}")
                return None
            except Exception as e:
                logger.error(f"wiretap_rw_frame failed: {e}")
                return None

    @staticmethod
    def _read_file_direct(file_path: str) -> Optional[bytes]:
        """Read an image file directly from disk, returning float32 RGB bytes.

        Used as a fallback when the Wiretap server can't access
        soft-imported media but the file is accessible locally
        (e.g. on a shared network mount).
        """
        if not os.path.isfile(file_path):
            logger.debug(f"Direct read: file not found locally: {file_path}")
            return None

        logger.info(f"Direct file read fallback: {file_path}")

        # Try OpenImageIO
        try:
            import OpenImageIO as oiio
            inp = oiio.ImageInput.open(file_path)
            if inp:
                spec = inp.spec()
                pixels = np.zeros(
                    (spec.height, spec.width, min(spec.nchannels, 3)),
                    dtype=np.float32,
                )
                inp.read_image(0, 0, 0, 3, oiio.FLOAT, pixels)
                inp.close()
                # Pad to 3 channels if needed
                if pixels.shape[2] < 3:
                    pad = np.zeros(
                        (spec.height, spec.width, 3 - pixels.shape[2]),
                        dtype=np.float32,
                    )
                    pixels = np.concatenate([pixels, pad], axis=2)
                logger.info(
                    f"Direct read via OIIO: {spec.width}x{spec.height} "
                    f"({spec.nchannels}ch)"
                )
                return pixels.tobytes()
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"OIIO direct read failed: {e}")

        # Try OpenEXR (EXR only)
        if file_path.lower().endswith(".exr"):
            try:
                import OpenEXR
                import Imath
                exr = OpenEXR.InputFile(file_path)
                header = exr.header()
                dw = header["dataWindow"]
                w = dw.max.x - dw.min.x + 1
                h = dw.max.y - dw.min.y + 1
                pt = Imath.PixelType(Imath.PixelType.FLOAT)
                r = np.frombuffer(exr.channel("R", pt), dtype=np.float32).reshape(h, w)
                g = np.frombuffer(exr.channel("G", pt), dtype=np.float32).reshape(h, w)
                b = np.frombuffer(exr.channel("B", pt), dtype=np.float32).reshape(h, w)
                pixels = np.stack([r, g, b], axis=-1)
                logger.info(f"Direct read via OpenEXR: {w}x{h}")
                return pixels.tobytes()
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"OpenEXR direct read failed: {e}")

        logger.error(
            f"Direct file read failed — no image library available. "
            f"Install OpenImageIO or OpenEXR."
        )
        return None

    # -----------------------------------------------------------------------
    # Write operations
    # -----------------------------------------------------------------------

    def build_clip_format(
        self,
        width: int,
        height: int,
        bit_depth: int = 32,
        fps: float = 24.0,
    ):
        """
        Construct a WireTapClipFormat for creating new clips.

        Defaults to 32-bit float RGB which matches ComfyUI's native
        float32 tensor format and avoids bit-packing complexity.

        Returns:
            WireTapClipFormat or None if SDK unavailable.
        """
        if not _wiretap_available:
            return None

        fmt = WireTapClipFormat()

        # Try setter methods (SDK version dependent)
        try:
            fmt.setWidth(width)
            fmt.setHeight(height)
            fmt.setNumChannels(3)
            fmt.setBitsPerPixel(bit_depth * 3)
            fmt.setFrameRate(fps)

            # Progressive scan
            _prog = getattr(
                WireTapClipFormat, "SCAN_FORMAT_PROGRESSIVE",
                getattr(
                    getattr(WireTapClipFormat, "ScanFormat", None),
                    "SCAN_FORMAT_PROGRESSIVE", 0,
                ),
            )
            fmt.setScanFormat(_prog)
            fmt.setPixelRatio(1.0)

            logger.info(
                f"Built clip format: {width}x{height} "
                f"{bit_depth}-bit {fps}fps"
            )
            return fmt
        except AttributeError as e:
            logger.error(f"WireTapClipFormat setter not available: {e}")
            return None

    def read_frame_via_gateway(
        self,
        hostname: str,
        file_path: str,
    ) -> Optional[Tuple[bytes, Any]]:
        """
        Read a file through the Gateway server to get a properly-encoded
        Wiretap frame buffer.  The Gateway handles all pixel format
        encoding so we never need to manually pack buffers.

        Args:
            hostname: Flame workstation hostname.
            file_path: Absolute path to an image file on disk.

        Returns:
            Tuple of (raw_buffer_bytes, WireTapClipFormat) or None.
        """
        if not _wiretap_available:
            logger.warning("Wiretap SDK not available — cannot read via Gateway")
            return None

        self.initialize()

        try:
            server = self._get_server_handle(hostname, "Gateway")
        except ConnectionError as e:
            logger.error(f"Gateway connection failed: {e}")
            return None

        # Gateway addresses files as "<path>@CLIP"
        gateway_node_id = f"{file_path}@CLIP"
        node_handle = WireTapNodeHandle(server, gateway_node_id)

        fmt = WireTapClipFormat()
        if not node_handle.getClipFormat(fmt):
            logger.error(
                f"Gateway getClipFormat failed for {gateway_node_id}: "
                f"{node_handle.lastError()}"
            )
            return None

        buf_size = fmt.frameBufferSize()
        if buf_size <= 0:
            logger.error(f"Gateway reports zero buffer size for {file_path}")
            return None

        # Read frame 0 (single-frame file)
        # Use CLI tool for the actual read since the Python readFrame has
        # the same str/bytes issue as IFFFS reads
        raw_bytes = self._read_frame_via_cli(
            hostname, gateway_node_id, 0, "Gateway"
        )
        if raw_bytes is None:
            logger.error(f"Gateway frame read failed for {file_path}")
            return None

        return (raw_bytes, fmt)

    def can_create_node(
        self,
        hostname: str,
        parent_node_id: str,
        node_type: str = "CLIP",
        server_type: str = "IFFFS",
    ) -> bool:
        """Check if a node type can be created under a parent node."""
        tool = _find_wiretap_tool("wiretap_can_create_node")
        if not tool:
            logger.warning("wiretap_can_create_node not found")
            return False

        try:
            result = subprocess.run(
                [tool, "-h", f"{hostname}:{server_type}",
                 "-n", parent_node_id, "-t", node_type],
                capture_output=True, text=True, timeout=10,
            )
            can = "can create" in result.stdout and "can NOT" not in result.stdout
            logger.debug(
                f"canCreateNode({node_type}) on {parent_node_id}: "
                f"{'yes' if can else 'no'}"
            )
            return can
        except Exception as e:
            logger.error(f"wiretap_can_create_node failed: {e}")
            return False

    def create_clip_node(
        self,
        hostname: str,
        parent_node_id: str,
        clip_name: str,
        width: int = 1920,
        height: int = 1080,
        bit_depth: int = 16,
        fps: float = 24.0,
        num_frames: int = 1,
        server_type: str = "IFFFS",
        colour_space: str = "",
    ) -> Optional[str]:
        """
        Create a new clip node via the wiretap_create_clip CLI tool.

        Uses the CLI tool instead of the Python SDK to avoid Boost.Python
        issues and to handle format setup + frame allocation in one step.

        Args:
            hostname: Flame workstation hostname.
            parent_node_id: Node ID of the parent reel or library.
            clip_name: Display name for the new clip.
            width: Frame width.
            height: Frame height.
            bit_depth: Bits per channel (8, 10, 12, 16, 32).
            fps: Frame rate.
            num_frames: Number of frames to allocate.
            server_type: Server type (normally "IFFFS").
            colour_space: OCIO colour space name for the clip.

        Returns:
            The new clip's node ID, or None on failure.
        """
        tool = _find_wiretap_tool("wiretap_create_clip")
        if not tool:
            logger.error("wiretap_create_clip not found")
            return None

        # Map bit_depth to BPP and format tag.
        # Flame conventions:
        #   8-bit:  24bpp, tag "rgb" (integer)
        #   10-bit: 30bpp, tag "rgb" (integer, packed in 32-bit words)
        #   12-bit: 48bpp, tag "rgb" (integer in 16-bit words; 36bpp NOT supported)
        #   16-bit: 48bpp, tag "rgb_float_le" (half-float)
        #   32-bit: 96bpp, tag "rgb_float_le" (single float)
        # Note: 12-bit and 16-bit both use 48bpp — the format tag
        # distinguishes integer (rgb) from float (rgb_float_le).
        bpp_map = {
            8:  (24, "rgb"),
            10: (30, "rgb"),
            12: (48, "rgb"),           # 12 data bits in 16-bit words
            16: (48, "rgb_float_le"),  # half-float
            32: (96, "rgb_float_le"),  # single float
        }
        bpp, fmt_tag = bpp_map.get(bit_depth, (48, "rgb_float_le"))

        cmd = [
            tool,
            "-h", f"{hostname}:{server_type}",
            "-n", parent_node_id,
            "-d", clip_name,
            "-x", str(width),
            "-y", str(height),
            "-b", str(bpp),
            "-r", str(fps),
            "-N", str(num_frames),
            "-s", "progressive",
            "-f", fmt_tag,
        ]
        if colour_space:
            cmd.extend(["-C", colour_space])
        logger.info(
            f"Creating clip '{clip_name}' {width}x{height} "
            f"{bit_depth}-bit {fps}fps ({num_frames} frames)"
        )
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            combined = f"{result.stdout} {result.stderr}".strip()

            # Parse the new node ID from output like:
            # "Created clip node '/projects/.../node_id'."
            import re
            match = re.search(r"Created clip node '([^']+)'", combined)
            if match:
                new_node_id = match.group(1)
                logger.info(f"Created clip '{clip_name}' at {new_node_id}")
                return new_node_id

            logger.error(f"wiretap_create_clip failed: {combined}")
            return None
        except subprocess.TimeoutExpired:
            logger.error("wiretap_create_clip timed out")
            return None
        except Exception as e:
            logger.error(f"wiretap_create_clip failed: {e}")
            return None

    def create_node(
        self,
        hostname: str,
        parent_node_id: str,
        node_type: str,
        display_name: str,
        server_type: str = "IFFFS",
    ) -> Optional[str]:
        """Create a generic node (LIBRARY, REEL, etc.) via CLI tool."""
        tool = _find_wiretap_tool("wiretap_create_node")
        if not tool:
            logger.error("wiretap_create_node not found")
            return None

        try:
            result = subprocess.run(
                [tool, "-h", f"{hostname}:{server_type}",
                 "-n", parent_node_id, "-t", node_type, "-d", display_name],
                capture_output=True, text=True, timeout=15,
            )
            combined = f"{result.stdout} {result.stderr}".strip()

            import re
            match = re.search(r"Created node '([^']+)'", combined)
            if match:
                new_node_id = match.group(1)
                logger.info(
                    f"Created {node_type} '{display_name}' at {new_node_id}"
                )
                return new_node_id

            logger.error(f"wiretap_create_node failed: {combined}")
            return None
        except Exception as e:
            logger.error(f"wiretap_create_node failed: {e}")
            return None

    def set_num_frames(
        self,
        hostname: str,
        node_id: str,
        num_frames: int,
        server_type: str = "IFFFS",
    ) -> bool:
        """Set the number of frames on a clip node via CLI tool."""
        tool = _find_wiretap_tool("wiretap_set_num_frames")
        if not tool:
            logger.error("wiretap_set_num_frames not found")
            return False

        try:
            result = subprocess.run(
                [tool, "-h", f"{hostname}:{server_type}",
                 "-n", node_id, "-N", str(num_frames)],
                capture_output=True, text=True, timeout=15,
            )
            combined = f"{result.stdout} {result.stderr}".strip()
            if result.returncode != 0:
                logger.error(
                    f"wiretap_set_num_frames failed: {combined}"
                )
                return False
            logger.info(f"Set {node_id} to {num_frames} frames")
            return True
        except Exception as e:
            logger.error(f"wiretap_set_num_frames failed: {e}")
            return False

    def write_frame(
        self,
        hostname: str,
        node_id: str,
        frame_number: int,
        buffer: bytes,
        buffer_size: int,
        server_type: str = "IFFFS",
    ) -> bool:
        """
        Write a single frame buffer to a clip node.

        Uses the wiretap_rw_frame CLI tool with -w flag to bypass the
        Boost.Python bytes/char const* mismatch (same issue as readFrame).

        Args:
            hostname: Flame workstation hostname.
            node_id: Clip (or hires sub-node) node ID.
            frame_number: 0-based frame index.
            buffer: Raw frame bytes matching the clip's native format.
            buffer_size: Size of the buffer in bytes.
            server_type: Server type (normally "IFFFS").

        Returns:
            True on success, False on failure.
        """
        return self._write_frame_via_cli(
            hostname, node_id, frame_number, buffer, server_type
        )

    def _write_frame_via_cli(
        self,
        hostname: str,
        node_id: str,
        frame_number: int,
        buffer: bytes,
        server_type: str,
    ) -> bool:
        """Write a frame using the wiretap_rw_frame CLI tool with -w flag.

        The CLI tool reads raw frame data from a file and pushes it into
        the Wiretap clip via the C++ writeFrame call internally, bypassing
        the Boost.Python str/bytes mismatch.

        File naming convention: the tool expects -f <base> and constructs
        <base>_<frame_index>.<ext>.  We write raw bytes to match that
        pattern so the tool finds the file.
        """
        import tempfile

        tool = _find_wiretap_tool("wiretap_rw_frame")
        if not tool:
            logger.error("wiretap_rw_frame not found — cannot write frame")
            return False

        with tempfile.TemporaryDirectory() as tmpdir:
            # The -i flag sets the destination frame index in the clip.
            # The tool always reads from {base}_0.{ext} (the file index
            # is always 0, independent of the clip frame index).
            base_path = os.path.join(tmpdir, "frame")
            raw_path = os.path.join(tmpdir, "frame_0.")
            with open(raw_path, "wb") as f:
                f.write(buffer)

            # Verify the file was written
            actual_size = os.path.getsize(raw_path)
            logger.debug(
                f"Temp file: {raw_path} size={actual_size} "
                f"exists={os.path.exists(raw_path)}"
            )

            cmd = [
                tool,
                "-h", f"{hostname}:{server_type}",
                "-n", node_id,
                "-i", str(frame_number),
                "-f", base_path,
                "-w",
            ]
            logger.info(
                f"CLI write frame {frame_number}: "
                f"node={node_id} buf_size={len(buffer)} "
                f"file={raw_path}"
            )
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60,
                )
                combined = f"{result.stdout} {result.stderr}".strip()
                if result.returncode != 0:
                    logger.error(
                        f"wiretap_rw_frame -w failed (rc={result.returncode}): "
                        f"{combined}"
                    )
                    return False
                if combined:
                    logger.info(f"wiretap_rw_frame -w output: {combined}")
                return True
            except subprocess.TimeoutExpired:
                logger.error(
                    f"wiretap_rw_frame -w timed out for frame {frame_number}"
                )
                return False
            except Exception as e:
                logger.error(f"wiretap_rw_frame -w failed: {e}")
                return False

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
