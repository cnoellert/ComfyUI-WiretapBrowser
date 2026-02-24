"""
Wiretap Metadata Node

Extracts all available metadata from a Flame clip via Wiretap,
exposing it as individual STRING outputs and a combined JSON output.
"""

import json
import logging
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional

from .wiretap_connection import get_connection_manager, is_wiretap_available

logger = logging.getLogger("ComfyUI-WiretapBrowser")


def _parse_clip_xml(xml_str: str) -> Dict[str, str]:
    """Parse the Wiretap XML metadata block into a flat dict."""
    result = {}
    if not xml_str:
        return result
    try:
        root = ET.fromstring(xml_str)
        # Wiretap XML is typically <XML><ClipData>...</ClipData></XML>
        clip_data = root.find("ClipData")
        if clip_data is None:
            clip_data = root
        for child in clip_data:
            if child.text:
                result[child.tag] = child.text
    except ET.ParseError as e:
        logger.warning(f"Failed to parse clip XML: {e}")
    return result


class WiretapMetadata:
    """
    Extract metadata from a Flame clip via Wiretap.

    Outputs individual metadata fields as STRING values plus
    a combined JSON string containing everything.

    Wire clip_node_id and hostname from the Browser node.
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "extract"
    RETURN_TYPES = ("STRING", "STRING", "STRING",
                    "STRING", "STRING", "STRING", "STRING", "STRING",
                    "STRING", "STRING", "STRING", "STRING", "FLOAT",
                    "INT", "INT", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("clip_node_id", "hostname", "server_type",
                    "colour_space", "tape_name", "timecode", "duration",
                    "drop_mode", "creation_date", "format_tag",
                    "clip_name", "proxy_format", "frame_rate",
                    "width", "height", "bit_depth", "pixel_ratio",
                    "metadata_json")
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip_node_id": ("STRING", {
                    "default": "",
                    "forceInput": True,
                    "description": "Wiretap node ID from the Browser node",
                }),
                "hostname": ("STRING", {
                    "default": "localhost",
                    "forceInput": True,
                    "description": "Flame workstation hostname",
                }),
                "server_type": ("STRING", {
                    "default": "IFFFS",
                    "forceInput": True,
                    "description": "IFFFS or Gateway",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return kwargs.get("clip_node_id", "")

    def extract(
        self,
        clip_node_id: str,
        hostname: str,
        server_type: str,
    ):
        empty = self._empty_result()
        if not clip_node_id:
            return empty

        mgr = get_connection_manager()

        # Get clip format info
        format_info = mgr.get_clip_format(hostname, clip_node_id, server_type)
        if not format_info:
            logger.error(f"Could not read clip format for {clip_node_id}")
            return empty

        # Get XML metadata from the clip
        xml_data = self._get_clip_metadata(mgr, hostname, clip_node_id, server_type)
        xml_fields = _parse_clip_xml(xml_data)

        # Get display name
        clip_name = self._get_display_name(mgr, hostname, clip_node_id, server_type)

        # Build combined metadata dict
        all_metadata = {
            "colour_space": format_info.get("colour_space", ""),
            "width": format_info.get("width", 0),
            "height": format_info.get("height", 0),
            "bit_depth": format_info.get("bit_depth", 0),
            "bits_per_pixel": format_info.get("bits_per_pixel", 0),
            "num_channels": format_info.get("num_channels", 0),
            "frame_rate": format_info.get("frame_rate", 0.0),
            "num_frames": format_info.get("num_frames", 0),
            "pixel_ratio": format_info.get("pixel_ratio", 1.0),
            "format_tag": format_info.get("format_tag", ""),
            "frame_buffer_size": format_info.get("frame_buffer_size", 0),
            "clip_name": clip_name,
            "node_id": clip_node_id,
        }
        all_metadata.update(xml_fields)

        return (
            clip_node_id,
            hostname,
            server_type,
            format_info.get("colour_space", ""),
            xml_fields.get("TapeName", ""),
            xml_fields.get("SrcTimecode", ""),
            xml_fields.get("Duration", ""),
            xml_fields.get("DropMode", ""),
            xml_fields.get("ClipCreationDate", ""),
            format_info.get("format_tag", ""),
            clip_name,
            xml_fields.get("ProxyFormat", ""),
            format_info.get("frame_rate", 0.0),
            format_info.get("width", 0),
            format_info.get("height", 0),
            format_info.get("bit_depth", 0),
            format_info.get("pixel_ratio", 1.0),
            json.dumps(all_metadata, indent=2),
        )

    def _get_clip_metadata(
        self, mgr, hostname: str, node_id: str, server_type: str
    ) -> str:
        """Read the XML metadata string from a clip via Wiretap."""
        if not is_wiretap_available():
            return ""

        try:
            from adsk.libwiretapPythonClientAPI import (
                WireTapNodeHandle,
                WireTapStr,
                WireTapClipFormat,
            )

            server = mgr._get_server_handle(hostname, server_type)
            node_handle = WireTapNodeHandle(server, node_id)

            fmt = WireTapClipFormat()
            if node_handle.getClipFormat(fmt):
                md = fmt.metaData()
                if md:
                    return str(md)
        except Exception as e:
            logger.warning(f"Failed to read clip metadata: {e}")

        return ""

    def _get_display_name(
        self, mgr, hostname: str, node_id: str, server_type: str
    ) -> str:
        """Get the display name of a clip node."""
        if not is_wiretap_available():
            return ""

        try:
            from adsk.libwiretapPythonClientAPI import (
                WireTapNodeHandle,
                WireTapStr,
            )

            server = mgr._get_server_handle(hostname, server_type)
            node_handle = WireTapNodeHandle(server, node_id)

            display_name = WireTapStr()
            if node_handle.getDisplayName(display_name):
                return str(display_name)
        except Exception as e:
            logger.warning(f"Failed to get display name: {e}")

        return ""

    def _empty_result(self):
        return ("", "", "", "", "", "", "", "", "", "", "", "", 0.0, 0, 0, 0, 1.0, "{}")
