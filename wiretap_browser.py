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
    WiretapNode,
    NodeType,
)

logger = logging.getLogger("ComfyUI-WiretapBrowser")


# ---------------------------------------------------------------------------
# API Routes - these power the frontend browser widget
# ---------------------------------------------------------------------------

@PromptServer.instance.routes.get("/wiretap/status")
async def wiretap_status(request):
    """Check Wiretap SDK availability."""
    return web.json_response({
        "available": is_wiretap_available(),
        "error": get_wiretap_import_error(),
        "mock_mode": not is_wiretap_available(),
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
        return web.json_response({
            "success": True,
            "parent": node_id,
            "children": [c.to_dict() for c in children],
        })
    except Exception as e:
        logger.error(f"Browse error: {e}", exc_info=True)
        return web.json_response({
            "success": False,
            "error": str(e),
            "children": [],
        }, status=500)


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
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "INT", "INT", "FLOAT")
    RETURN_NAMES = (
        "clip_node_id",
        "hostname",
        "server_type",
        "width",
        "height",
        "num_frames",
        "fps",
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

        if clip_node_id:
            try:
                mgr = get_connection_manager()
                info = mgr.get_clip_format(hostname, clip_node_id, server_type)
                if info:
                    width = info.get("width", 0)
                    height = info.get("height", 0)
                    num_frames = info.get("num_frames", 0)
                    fps = info.get("frame_rate", 0.0)
            except Exception as e:
                logger.error(f"Error fetching clip info: {e}")

        return (clip_node_id, hostname, server_type, width, height, num_frames, fps)


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

        status_lines.append(f"Wiretap SDK: {'Available' if is_wiretap_available() else 'NOT FOUND (mock mode)'}")
        if not is_wiretap_available():
            status_lines.append(f"Import error: {get_wiretap_import_error()}")

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
