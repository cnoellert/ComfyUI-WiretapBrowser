"""
ComfyUI Wiretap Browser Node

Provides a tree-browser interface to navigate the Autodesk Flame
Wiretap IFFFS hierarchy and select clips for loading.
"""

import json
import logging
from typing import Dict, Any, List, Optional

from server import PromptServer
from aiohttp import web

from .wiretap_connection import (
    get_connection_manager,
    is_wiretap_available,
    get_wiretap_import_error,
    get_sdk_diagnostics,
    WiretapNode,
    NodeType,
)

logger = logging.getLogger("ComfyUI-WiretapBrowser")


# ---------------------------------------------------------------------------
# API Routes - these power the frontend browser widget
# ---------------------------------------------------------------------------

@PromptServer.instance.routes.get("/wiretap/status")
async def wiretap_status(request):
    """Check Wiretap SDK availability and diagnostics."""
    diag = get_sdk_diagnostics()
    return web.json_response({
        "available": diag["sdk_available"],
        "error": diag.get("import_error"),
        "mock_mode": not diag["sdk_available"],
        "diagnostics": diag,
    })


@PromptServer.instance.routes.get("/wiretap/browse")
async def wiretap_browse(request):
    """
    Browse the Wiretap hierarchy.

    Query params:
        hostname: Flame workstation hostname/IP
        node_id: Parent node path (default: "/")
        server_type: "IFFFS" or "Gateway" (default: "IFFFS")
    """
    hostname = request.query.get("hostname", "localhost")
    node_id = request.query.get("node_id", "/")
    server_type = request.query.get("server_type", "IFFFS")
    refresh = request.query.get("refresh", "")

    try:
        mgr = get_connection_manager()
        if refresh:
            mgr.invalidate_server(hostname, server_type)
        children = mgr.get_children(hostname, node_id, server_type)
        resp = web.json_response({
            "success": True,
            "parent": node_id,
            "children": [c.to_dict() for c in children],
        })
        # Prevent browser/proxy caching of tree listings
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp
    except Exception as e:
        logger.error(f"Browse error: {e}", exc_info=True)
        return web.json_response({
            "success": False,
            "error": str(e),
            "children": [],
        }, status=500)


@PromptServer.instance.routes.post("/wiretap/create_node")
async def wiretap_create_node(request):
    """
    Create a new node (LIBRARY, REEL, etc.) under a parent.

    JSON body:
        hostname: Flame workstation hostname/IP
        parent_node_id: Parent node path
        node_type: Type to create (e.g. "LIBRARY", "REEL")
        display_name: Name for the new node
        server_type: "IFFFS" (default)
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "error": "Invalid JSON body"}, status=400
        )

    hostname = body.get("hostname", "localhost")
    parent_node_id = body.get("parent_node_id", "")
    node_type = body.get("node_type", "")
    display_name = body.get("display_name", "")
    server_type = body.get("server_type", "IFFFS")

    if not parent_node_id or not node_type or not display_name:
        return web.json_response(
            {"success": False, "error": "parent_node_id, node_type, and display_name required"},
            status=400,
        )

    try:
        mgr = get_connection_manager()
        new_id = mgr.create_node(
            hostname, parent_node_id, node_type, display_name, server_type
        )
        if new_id:
            return web.json_response({
                "success": True,
                "node_id": new_id,
                "display_name": display_name,
                "node_type": node_type,
            })
        else:
            return web.json_response(
                {"success": False, "error": f"Failed to create {node_type} '{display_name}'"},
                status=500,
            )
    except Exception as e:
        logger.error(f"Create node error: {e}", exc_info=True)
        return web.json_response(
            {"success": False, "error": str(e)}, status=500
        )


@PromptServer.instance.routes.get("/wiretap/clip_info")
async def wiretap_clip_info(request):
    """
    Get detailed clip format information.

    Query params:
        hostname: Flame workstation hostname/IP
        node_id: Clip node ID
        server_type: "IFFFS" or "Gateway" (default: "IFFFS")
    """
    hostname = request.query.get("hostname", "localhost")
    node_id = request.query.get("node_id", "")
    server_type = request.query.get("server_type", "IFFFS")

    if not node_id:
        return web.json_response(
            {"success": False, "error": "node_id required"}, status=400
        )

    try:
        mgr = get_connection_manager()
        info = mgr.get_clip_format(hostname, node_id, server_type)
        if info:
            return web.json_response({"success": True, "format": info})
        else:
            return web.json_response(
                {"success": False, "error": "Could not read clip format"},
                status=404,
            )
    except Exception as e:
        logger.error(f"Clip info error: {e}", exc_info=True)
        return web.json_response(
            {"success": False, "error": str(e)}, status=500
        )


# ---------------------------------------------------------------------------
# ComfyUI Node: Wiretap Browser
# ---------------------------------------------------------------------------

class WiretapBrowser:
    """
    Browse and select clips from an Autodesk Flame Wiretap server.

    This node provides a visual tree browser (via the frontend JavaScript
    extension) to navigate the IFFFS hierarchy:
      Server → Projects → Libraries → Reels → Clips

    The selected clip's node_id and server info are output as strings
    that can be connected to the WiretapClipLoader node.
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "select_clip"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "INT", "INT", "FLOAT", "STRING")
    RETURN_NAMES = (
        "clip_node_id",
        "hostname",
        "server_type",
        "width",
        "height",
        "num_frames",
        "fps",
        "colour_space",
    )
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "hostname": ("STRING", {
                    "default": "localhost",
                    "multiline": False,
                    "description": "Flame workstation hostname or IP address",
                }),
                "clip_node_id": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "description": (
                        "Wiretap node ID of the selected clip. "
                        "Use the browser widget to browse and select."
                    ),
                }),
                "server_type": (["IFFFS", "Gateway"], {
                    "default": "IFFFS",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        """Force re-execution when the clip selection changes."""
        return kwargs.get("clip_node_id", "")

    def select_clip(
        self, hostname: str, clip_node_id: str, server_type: str
    ):
        """
        Output the selected clip's metadata for downstream nodes.
        """
        width = 0
        height = 0
        num_frames = 0
        fps = 0.0
        colour_space = ""

        if clip_node_id:
            try:
                mgr = get_connection_manager()
                info = mgr.get_clip_format(hostname, clip_node_id, server_type)
                if info:
                    width = info.get("width", 0)
                    height = info.get("height", 0)
                    num_frames = info.get("num_frames", 0)
                    fps = info.get("frame_rate", 0.0)
                    colour_space = info.get("colour_space", "")
            except Exception as e:
                logger.error(f"Error fetching clip info: {e}")

        return (clip_node_id, hostname, server_type, width, height, num_frames, fps, colour_space)


# ---------------------------------------------------------------------------
# ComfyUI Node: Wiretap Server Scanner
# ---------------------------------------------------------------------------

class WiretapServerInfo:
    """
    Display connection status and server information for a Wiretap host.
    Useful for debugging connectivity before loading clips.
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "get_info"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status_info",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "hostname": ("STRING", {
                    "default": "localhost",
                    "multiline": False,
                }),
            },
        }

    def get_info(self, hostname: str):
        status_lines = []

        # SDK diagnostics
        diag = get_sdk_diagnostics()
        if diag["sdk_available"]:
            status_lines.append(f"Wiretap SDK: Available")
            if "sdk_path" in diag:
                status_lines.append(f"  Module: {diag['sdk_path']}")
        else:
            status_lines.append("Wiretap SDK: NOT FOUND (mock mode)")
            status_lines.append(f"  Error: {diag.get('import_error', 'unknown')}")
            status_lines.append(f"  Python: {diag['python_version']} (SDK requires 3.11)")
            status_lines.append(
                "  Install: https://aps.autodesk.com/developer/overview/wiretap"
            )
            status_lines.append(
                "  Or set WIRETAP_SDK_PATH, WIRETAP_TOOLS_DIR, WIRETAP_LIB_DIR"
            )

        # CLI tools
        status_lines.append("")
        status_lines.append("CLI Tools:")
        for name, path in diag["cli_tools"].items():
            status_lines.append(f"  {name}: {path}")

        # Environment overrides
        if diag["env_overrides"]:
            status_lines.append("")
            status_lines.append("Environment overrides:")
            for var, val in diag["env_overrides"].items():
                status_lines.append(f"  {var}={val}")

        # Server connectivity
        status_lines.append("")
        status_lines.append(f"Target host: {hostname}")
        try:
            mgr = get_connection_manager()
            children = mgr.get_children(hostname, "/projects", "IFFFS")
            status_lines.append(f"Projects found: {len(children)}")
            for child in children:
                status_lines.append(f"  - {child.display_name} ({child.node_type.value})")
        except Exception as e:
            status_lines.append(f"Connection error: {e}")

        info_text = "\n".join(status_lines)

        # Send status to frontend for display
        PromptServer.instance.send_sync(
            "wiretap.server.status",
            {"hostname": hostname, "status": info_text},
        )

        return (info_text,)
