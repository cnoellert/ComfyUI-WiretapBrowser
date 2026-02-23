"""
ComfyUI Wiretap Clip Loader Node

Reads frame data from an Autodesk Flame clip via the Wiretap SDK
and outputs ComfyUI IMAGE tensors for use in AI workflows.
"""

import logging
import torch
import numpy as np
from typing import Optional, Tuple

from .wiretap_connection import get_connection_manager, is_wiretap_available
from .frame_converter import raw_rgb_to_tensor, batch_frames_to_tensor

logger = logging.getLogger("ComfyUI-WiretapBrowser")


class WiretapClipLoader:
    """
    Load frames from an Autodesk Flame clip via Wiretap.

    Reads one or more frames from a clip selected via the WiretapBrowser
    node (or by directly specifying a Wiretap node ID) and outputs them
    as a ComfyUI IMAGE batch tensor.

    Supports all Flame raw RGB bit depths:
    - 8-bit integer
    - 10-bit integer (filled to 32-bit words)
    - 12-bit packed and unpacked
    - 16-bit half-float
    - 32-bit float
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "load_frames"
    RETURN_TYPES = ("IMAGE", "INT", "INT", "INT", "FLOAT")
    RETURN_NAMES = ("images", "width", "height", "frame_count", "fps")
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip_node_id": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "forceInput": True,
                    "description": "Wiretap node ID from the Browser node",
                }),
                "hostname": ("STRING", {
                    "default": "localhost",
                    "multiline": False,
                    "forceInput": True,
                    "description": "Flame workstation hostname",
                }),
                "server_type": ("STRING", {
                    "default": "IFFFS",
                    "forceInput": True,
                    "description": "IFFFS or Gateway",
                }),
                "start_frame": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 999999,
                    "step": 1,
                    "description": "First frame to load (0-indexed)",
                }),
                "frame_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 9999,
                    "step": 1,
                    "description": "Number of frames to load",
                }),
            },
            "optional": {
                "max_dimension": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 8192,
                    "step": 64,
                    "description": (
                        "Max width or height to resize to (0 = no resize). "
                        "Useful for large Flame clips."
                    ),
                }),
                "use_hires": ("BOOLEAN", {
                    "default": True,
                    "description": "Read from hires sub-node if available",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        """Force re-execution when inputs change."""
        return (
            kwargs.get("clip_node_id", ""),
            kwargs.get("start_frame", 0),
            kwargs.get("frame_count", 1),
        )

    def load_frames(
        self,
        clip_node_id: str,
        hostname: str,
        server_type: str,
        start_frame: int,
        frame_count: int,
        max_dimension: int = 0,
        use_hires: bool = True,
    ):
        """
        Load frames from Flame via Wiretap and convert to IMAGE tensors.
        """
        if not clip_node_id:
            logger.warning("No clip_node_id provided, returning empty tensor")
            return self._empty_result()

        mgr = get_connection_manager()

        # Optionally resolve to hires sub-node
        actual_node_id = clip_node_id
        if use_hires and not clip_node_id.endswith("/hires"):
            # Check if a hires sub-node exists
            try:
                children = mgr.get_children(hostname, clip_node_id, server_type)
                for child in children:
                    if child.node_type.value == "HIRES":
                        actual_node_id = child.node_id
                        logger.info(f"Using hires sub-node: {actual_node_id}")
                        break
            except Exception as e:
                logger.warning(f"Could not check for hires node: {e}")

        # Get clip format
        format_info = mgr.get_clip_format(hostname, actual_node_id, server_type)
        if not format_info:
            logger.error(f"Could not read clip format for {actual_node_id}")
            return self._empty_result()

        total_frames = format_info.get("num_frames", 0)
        fps = format_info.get("frame_rate", 24.0)
        width = format_info.get("width", 0)
        height = format_info.get("height", 0)

        # Clamp frame range
        if start_frame >= total_frames:
            start_frame = max(0, total_frames - 1)
        end_frame = min(start_frame + frame_count, total_frames)
        actual_count = end_frame - start_frame

        if actual_count <= 0:
            logger.warning("No frames in range")
            return self._empty_result()

        logger.info(
            f"Loading {actual_count} frames from {actual_node_id} "
            f"(frames {start_frame}-{end_frame - 1})"
        )

        # Read frames
        raw_frames = []
        for frame_idx in range(start_frame, end_frame):
            result = mgr.read_frame(hostname, actual_node_id, frame_idx, server_type)
            if result is None:
                logger.error(f"Failed to read frame {frame_idx}")
                # Insert a black frame as placeholder
                buf_size = format_info.get("frame_buffer_size", width * height * 3)
                raw_frames.append(bytes(buf_size))
            else:
                raw_bytes, _ = result
                raw_frames.append(raw_bytes)

        # Convert to tensor batch
        images = batch_frames_to_tensor(raw_frames, format_info)

        # Optional resize
        if max_dimension > 0 and (width > max_dimension or height > max_dimension):
            images = self._resize_batch(images, max_dimension)
            _, height, width, _ = images.shape

        logger.info(
            f"Loaded {images.shape[0]} frames, "
            f"resolution {width}x{height}, "
            f"tensor shape {images.shape}"
        )

        return (images, width, height, actual_count, fps)

    def _resize_batch(
        self, images: torch.Tensor, max_dim: int
    ) -> torch.Tensor:
        """Resize a batch of images so the largest dimension <= max_dim."""
        _, h, w, c = images.shape
        scale = max_dim / max(h, w)
        if scale >= 1.0:
            return images

        new_h = max(1, int(h * scale))
        new_w = max(1, int(w * scale))

        # ComfyUI images are BHWC; torch interpolate expects BCHW
        images_bchw = images.permute(0, 3, 1, 2)
        resized = torch.nn.functional.interpolate(
            images_bchw,
            size=(new_h, new_w),
            mode="bilinear",
            align_corners=False,
        )
        return resized.permute(0, 2, 3, 1)

    def _empty_result(self):
        """Return a minimal valid empty result."""
        empty = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
        return (empty, 64, 64, 0, 24.0)


class WiretapFrameWriter:
    """
    Write IMAGE tensor frames back to a Flame clip via Wiretap.

    This is the round-trip companion to WiretapClipLoader — after
    processing frames through AI nodes, write the results back to
    a new or existing clip in Flame.

    NOTE: Writing frames via Wiretap is complex (as noted in community
    forums). This node uses the Gateway server workaround: write frames
    to temp files on disk, then read them back through the Gateway server
    and write to the IFFFS destination. This is the approach used by
    TimewarpML and others.
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "write_frames"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_node_id",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "hostname": ("STRING", {
                    "default": "localhost",
                    "multiline": False,
                }),
                "destination_node_id": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "description": (
                        "Wiretap node ID of the destination "
                        "(library or reel to write into)"
                    ),
                }),
                "clip_name": ("STRING", {
                    "default": "comfyui_output",
                    "multiline": False,
                }),
                "fps": ("FLOAT", {
                    "default": 24.0,
                    "min": 1.0,
                    "max": 120.0,
                    "step": 0.001,
                }),
            },
        }

    def write_frames(
        self,
        images: torch.Tensor,
        hostname: str,
        destination_node_id: str,
        clip_name: str,
        fps: float,
    ):
        """
        Write processed frames back to Flame via Wiretap.

        Uses the Gateway server workaround:
        1. Save frames as temp EXR/DPX files
        2. Read via Gateway server into a buffer
        3. Write buffer to IFFFS destination clip
        """
        if not destination_node_id:
            logger.error("No destination_node_id provided")
            return ("",)

        if not is_wiretap_available():
            logger.warning(
                "Wiretap SDK not available. In mock mode, frames would be "
                f"written to {destination_node_id}/{clip_name}"
            )
            return (f"{destination_node_id}/{clip_name}_mock",)

        import tempfile
        import os

        mgr = get_connection_manager()
        mgr.initialize()

        batch_size, h, w, c = images.shape
        logger.info(
            f"Writing {batch_size} frames ({w}x{h}) to "
            f"{destination_node_id}/{clip_name}"
        )

        # Step 1: Save frames to temp directory as EXR files
        temp_dir = tempfile.mkdtemp(prefix="comfyui_wiretap_")
        temp_files = []

        try:
            for i in range(batch_size):
                frame = images[i].cpu().numpy()
                frame_uint16 = (np.clip(frame, 0.0, 1.0) * 65535).astype(np.uint16)

                # Save as raw 16-bit RGB for Gateway to pick up
                file_path = os.path.join(temp_dir, f"frame_{i:06d}.exr")
                self._save_frame_exr(frame, file_path)
                temp_files.append(file_path)

            # Step 2 & 3: Use Wiretap Gateway + IFFFS to create the clip
            result_node_id = self._create_clip_via_gateway(
                mgr, hostname, destination_node_id, clip_name,
                temp_files, w, h, fps,
            )

            return (result_node_id,)

        except Exception as e:
            logger.error(f"Error writing frames: {e}", exc_info=True)
            return ("",)

        finally:
            # Cleanup temp files
            for f in temp_files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass

    def _save_frame_exr(self, frame_np: np.ndarray, path: str):
        """Save a frame as EXR using OpenEXR if available, otherwise fallback to raw."""
        try:
            import OpenEXR
            import Imath

            h, w, c = frame_np.shape
            header = OpenEXR.Header(w, h)
            float_chan = Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
            header["channels"] = {"R": float_chan, "G": float_chan, "B": float_chan}

            r = frame_np[:, :, 0].astype(np.float32).tobytes()
            g = frame_np[:, :, 1].astype(np.float32).tobytes()
            b = frame_np[:, :, 2].astype(np.float32).tobytes()

            out = OpenEXR.OutputFile(path, header)
            out.writePixels({"R": r, "G": g, "B": b})
            out.close()

        except ImportError:
            # Fallback: save as raw RGB float32 with a simple header
            import struct
            h, w, c = frame_np.shape
            with open(path, "wb") as f:
                f.write(struct.pack("III", w, h, c))
                f.write(frame_np.astype(np.float32).tobytes())

    def _create_clip_via_gateway(
        self, mgr, hostname, dest_node_id, clip_name,
        file_paths, width, height, fps,
    ) -> str:
        """
        Create a clip in Flame using the Gateway server workaround.

        This reads each temp file through the Gateway server (which decodes
        the format to raw RGB) and then writes the buffer to an IFFFS clip.
        """
        from adsk.libwiretapPythonClientAPI import (
            WireTapNodeHandle,
            WireTapClipFormat,
            WireTapStr,
        )

        # Create the destination clip — use the connection manager's probe
        # logic to find the right WireTapServerId constructor signature.
        server_handle = mgr._get_server_handle(hostname, "IFFFS")
        parent_handle = WireTapNodeHandle(server_handle, dest_node_id)

        clip_format = WireTapClipFormat(
            width, height,
            3 * 16,  # bits per pixel (16-bit per channel)
            3,        # num channels
            fps,      # frame rate
            1,        # pixel ratio
            getattr(
                WireTapClipFormat, "SCAN_FORMAT_PROGRESSIVE",
                getattr(
                    getattr(WireTapClipFormat, "ScanFormat", None),
                    "SCAN_FORMAT_PROGRESSIVE", 0,
                ),
            ),
            WireTapClipFormat.FORMAT_RGB(),
        )

        new_clip = WireTapNodeHandle()
        if not parent_handle.createClipNode(
            clip_name, clip_format, "CLIP", new_clip
        ):
            raise RuntimeError(
                f"Failed to create clip: {parent_handle.lastError()}"
            )

        if not new_clip.setNumFrames(len(file_paths)):
            raise RuntimeError(
                f"Failed to set frame count: {new_clip.lastError()}"
            )

        # Read each file through Gateway and write to IFFFS
        gateway_handle = mgr._get_server_handle(hostname, "Gateway")

        dest_fmt = WireTapClipFormat()
        if not new_clip.getClipFormat(dest_fmt):
            raise RuntimeError(
                f"Failed to get dest format: {new_clip.lastError()}"
            )

        for frame_num, file_path in enumerate(file_paths):
            # Read via Gateway
            gw_node = WireTapNodeHandle(
                gateway_handle, file_path + "@CLIP"
            )
            gw_fmt = WireTapClipFormat()
            if not gw_node.getClipFormat(gw_fmt):
                logger.warning(
                    f"Gateway read failed for {file_path}: {gw_node.lastError()}"
                )
                continue

            buff = "\0" * gw_fmt.frameBufferSize()
            if not gw_node.readFrame(0, buff, gw_fmt.frameBufferSize()):
                logger.warning(f"Failed to read frame from gateway: {gw_node.lastError()}")
                continue

            # Write to IFFFS clip
            if not new_clip.writeFrame(
                frame_num, buff, dest_fmt.frameBufferSize()
            ):
                logger.warning(
                    f"Failed to write frame {frame_num}: {new_clip.lastError()}"
                )

        result_id = WireTapStr()
        new_clip.getNodeId(result_id)
        logger.info(f"Created clip: {str(result_id)}")
        return str(result_id)
