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
    RETURN_TYPES = ("IMAGE", "INT", "INT", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("images", "width", "height", "frame_count", "fps", "colour_space")
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
        colour_space = format_info.get("colour_space", "")

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

        return (images, width, height, actual_count, fps, colour_space)

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
        return (empty, 64, 64, 0, 24.0, "")


class WiretapFrameWriter:
    """
    Write IMAGE tensor frames back to Flame via Wiretap or to disk as EXR.

    Two modes of operation:

    1. **Wiretap round-trip** (destination_node_id wired):
       Saves temp EXR → reads through Gateway server (proper encoding) →
       createClipNode on IFFFS → writeFrame. Full round-trip back into Flame.

    2. **Disk-only** (no destination wired):
       Saves EXR sequence to output_directory for manual import via MediaHub.

    The Gateway round-trip avoids manual buffer packing — the Gateway server
    handles all pixel format encoding when reading the temp EXR back.
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "write_frames"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output_path",)
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "output_directory": ("STRING", {
                    "default": "/var/tmp/comfyui_output",
                    "multiline": False,
                    "description": (
                        "Directory for EXR output (disk mode) or temp files "
                        "(Wiretap mode)"
                    ),
                }),
                "clip_name": ("STRING", {
                    "default": "comfyui_output",
                    "multiline": False,
                    "description": "Base name for the image sequence / Flame clip",
                }),
                "start_frame": ("INT", {
                    "default": 1001,
                    "min": 0,
                    "max": 9999999,
                    "step": 1,
                    "description": "Starting frame number for the sequence",
                }),
                "hostname": ("STRING", {
                    "default": "localhost",
                    "multiline": False,
                    "description": "Flame workstation hostname",
                }),
                "server_type": (["IFFFS", "Gateway"], {
                    "default": "IFFFS",
                }),
                "destination_node_id": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "description": (
                        "Destination reel/library node ID. "
                        "Use the Browse Destination button to select. "
                        "Leave empty for disk-only mode."
                    ),
                }),
            },
            "optional": {
                "colour_space": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "forceInput": True,
                    "description": "Colour space to embed in EXR metadata",
                }),
            },
        }

    def write_frames(
        self,
        images: torch.Tensor,
        output_directory: str,
        clip_name: str,
        start_frame: int,
        hostname: str,
        server_type: str,
        destination_node_id: str,
        colour_space: str = "",
    ):
        """Write frames to Flame via Wiretap round-trip or to disk as EXR."""
        import os
        import tempfile

        if not output_directory:
            logger.error("No output_directory provided")
            return ("",)

        batch_size, h, w, c = images.shape
        wiretap_mode = bool(destination_node_id and destination_node_id.strip())

        if wiretap_mode:
            # --- Wiretap round-trip mode ---
            return self._write_wiretap(
                images, output_directory, clip_name, start_frame,
                colour_space, destination_node_id.strip(), hostname,
                server_type,
            )
        else:
            # --- Disk-only mode (existing behavior) ---
            return self._write_disk(
                images, output_directory, clip_name, start_frame, colour_space
            )

    def _write_disk(
        self,
        images: torch.Tensor,
        output_directory: str,
        clip_name: str,
        start_frame: int,
        colour_space: str,
    ):
        """Save frames as an EXR image sequence to disk."""
        import os

        seq_dir = os.path.join(output_directory, clip_name)
        os.makedirs(seq_dir, exist_ok=True)

        batch_size, h, w, c = images.shape
        logger.info(f"Writing {batch_size} frames ({w}x{h}) to {seq_dir}")

        written = 0
        for i in range(batch_size):
            frame_num = start_frame + i
            frame = images[i].cpu().numpy()
            file_path = os.path.join(
                seq_dir, f"{clip_name}.{frame_num:07d}.exr"
            )
            if self._save_frame_exr(frame, file_path, colour_space):
                written += 1

        logger.info(f"Wrote {written}/{batch_size} frames to {seq_dir}")
        return (seq_dir,)

    def _write_wiretap(
        self,
        images: torch.Tensor,
        output_directory: str,
        clip_name: str,
        start_frame: int,
        colour_space: str,
        destination_node_id: str,
        hostname: str,
        server_type: str,
    ):
        """
        Write frames into Flame via Wiretap CLI tools.

        All operations use CLI tools (wiretap_create_clip, wiretap_rw_frame)
        to bypass Boost.Python str/bytes incompatibilities in the Python SDK.

        For existing clips: resolves to hires sub-node and writes directly.
        For reels/libraries: validates with canCreateNode, then creates clip.

        Falls back to disk-only mode on any Wiretap error.
        """
        import os

        mgr = get_connection_manager()
        batch_size, h, w, c = images.shape

        try:
            # Detect whether destination is a reel (create new clip)
            # or an existing clip (write directly into it)
            children = mgr.get_children(hostname, destination_node_id, server_type)
            is_clip = any(
                ch.node_type.value in ("HIRES", "LOWRES")
                for ch in children
            )

            if is_clip:
                # Destination is an existing clip — resolve to hires sub-node
                hires_id = destination_node_id
                for ch in children:
                    if ch.node_type.value == "HIRES":
                        hires_id = ch.node_id
                        break
                target_clip_id = hires_id
                logger.info(
                    f"Writing into existing clip: {target_clip_id}"
                )

                # Query the actual format of the existing clip
                clip_info = mgr.get_clip_format(
                    hostname, target_clip_id, server_type
                )
                if clip_info:
                    logger.info(
                        f"Target clip format: {clip_info.get('width')}x"
                        f"{clip_info.get('height')} "
                        f"{clip_info.get('bit_depth')}-bit "
                        f"buf_size={clip_info.get('frame_buffer_size')}"
                    )
            else:
                # Destination is a reel/library — create a new clip
                # Validate first
                if not mgr.can_create_node(
                    hostname, destination_node_id, "CLIP", server_type
                ):
                    logger.warning(
                        f"Cannot create CLIP under {destination_node_id} "
                        f"— Wiretap cannot write into a Workspace that "
                        f"is currently open in Flame. Use a Shared Library "
                        f"instead. Falling back to disk mode."
                    )
                    return self._write_disk(
                        images, output_directory, clip_name,
                        start_frame, colour_space,
                    )

                target_clip_id = mgr.create_clip_node(
                    hostname, destination_node_id, clip_name,
                    width=w, height=h, bit_depth=10, fps=24.0,
                    num_frames=batch_size, server_type=server_type,
                )
                if target_clip_id is None:
                    logger.warning(
                        f"create_clip_node failed on {destination_node_id} — "
                        f"falling back to disk mode"
                    )
                    return self._write_disk(
                        images, output_directory, clip_name,
                        start_frame, colour_space,
                    )

                # wiretap_create_clip always creates a hires sub-node.
                # Append /hires directly — get_children can fail on
                # freshly-created clips ("Entry not found").
                hires_id = f"{target_clip_id}/hires"
                logger.info(f"Using hires sub-node: {hires_id}")
                target_clip_id = hires_id

                # Ensure hires node has frames allocated
                mgr.set_num_frames(
                    hostname, target_clip_id, batch_size, server_type
                )

                # We just created the clip — use known parameters
                # instead of querying (get_clip_format can fail on
                # freshly-created nodes).
                clip_info = {
                    "width": w,
                    "height": h,
                    "bit_depth": 10,
                    "bits_per_pixel": 30,
                    "num_channels": 3,
                    "frame_buffer_size": w * h * 4,  # 10-bit = 4 bytes/pixel
                }
                logger.info(
                    f"Target clip format (from creation params): "
                    f"{w}x{h} 10-bit"
                )

            # Write each frame via CLI tool
            written = 0
            for i in range(batch_size):
                frame = images[i].cpu().numpy()
                raw_bytes = self._encode_frame_for_clip(
                    frame, clip_info
                )

                if mgr.write_frame(
                    hostname, target_clip_id, i,
                    raw_bytes, len(raw_bytes), server_type,
                ):
                    written += 1
                else:
                    logger.error(f"writeFrame failed for frame {i}")

            logger.info(
                f"Wiretap write complete: {written}/{batch_size} frames "
                f"to clip '{clip_name}' ({target_clip_id})"
            )

            # Also save to disk as backup
            self._write_disk(
                images, output_directory, clip_name,
                start_frame, colour_space,
            )

            return (target_clip_id,)

        except Exception as e:
            logger.error(f"Wiretap write failed: {e} — falling back to disk mode")
            return self._write_disk(
                images, output_directory, clip_name,
                start_frame, colour_space,
            )

    @staticmethod
    def _encode_frame_for_clip(
        frame_np: np.ndarray,
        clip_info: Optional[dict],
    ) -> bytes:
        """
        Encode a float32 HWC frame into raw bytes matching the clip's
        native format so wiretap_rw_frame can write it directly.

        Wiretap stores frames bottom-to-top (origin at bottom-left).
        """
        frame_f32 = np.clip(frame_np, 0.0, 1.0).astype(np.float32)
        # Wiretap stores frames bottom-to-top
        frame_f32 = np.flipud(frame_f32)
        h, w, c = frame_f32.shape

        bit_depth = 32
        if clip_info:
            bit_depth = clip_info.get("bit_depth", 32)

        if bit_depth == 10:
            # Pack to 10-bit DPX method A: [10R][10G][10B][2pad] per uint32
            # Big-endian, MSB-first
            r = (frame_f32[:, :, 0] * 1023.0 + 0.5).astype(np.uint32)
            g = (frame_f32[:, :, 1] * 1023.0 + 0.5).astype(np.uint32)
            b = (frame_f32[:, :, 2] * 1023.0 + 0.5).astype(np.uint32)
            packed = (r << 22) | (g << 12) | (b << 2)
            return packed.astype(">u4").tobytes()

        elif bit_depth == 16:
            # 16-bit half-float interleaved RGB
            frame_f16 = frame_f32.astype(np.float16)
            return np.ascontiguousarray(frame_f16).tobytes()

        elif bit_depth == 12:
            # 12-bit packed into 16-bit words per channel
            r = (frame_f32[:, :, 0] * 4095.0 + 0.5).astype(np.uint16)
            g = (frame_f32[:, :, 1] * 4095.0 + 0.5).astype(np.uint16)
            b = (frame_f32[:, :, 2] * 4095.0 + 0.5).astype(np.uint16)
            interleaved = np.stack([r, g, b], axis=-1)
            return np.ascontiguousarray(interleaved).tobytes()

        elif bit_depth == 8:
            # 8-bit unsigned int RGB
            frame_u8 = (frame_f32 * 255.0 + 0.5).astype(np.uint8)
            return np.ascontiguousarray(frame_u8).tobytes()

        else:
            # Default: 32-bit float RGB interleaved
            return np.ascontiguousarray(frame_f32).tobytes()

    @staticmethod
    def _cleanup_tmp(tmp_dir: str):
        """Remove temporary EXR files."""
        import os
        import shutil
        try:
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir)
                logger.debug(f"Cleaned up temp dir: {tmp_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean up {tmp_dir}: {e}")

    def _save_frame_exr(
        self, frame_np: np.ndarray, path: str, colour_space: str = ""
    ) -> bool:
        """Save a single frame as a half-float (16-bit) EXR file.

        Tries OpenEXR first, then OpenImageIO, then falls back to
        a 16-bit TIFF via PIL.
        """
        frame_f32 = np.clip(frame_np, 0.0, 1.0).astype(np.float32)
        h, w, c = frame_f32.shape

        # Try OpenEXR — write as half-float (16-bit)
        try:
            import OpenEXR
            import Imath

            header = OpenEXR.Header(w, h)
            half_chan = Imath.Channel(Imath.PixelType(Imath.PixelType.HALF))
            header["channels"] = {
                "R": half_chan, "G": half_chan, "B": half_chan
            }
            if colour_space:
                header["chromaticities"] = colour_space

            frame_f16 = frame_f32.astype(np.float16)
            r = frame_f16[:, :, 0].tobytes()
            g = frame_f16[:, :, 1].tobytes()
            b = frame_f16[:, :, 2].tobytes()

            out = OpenEXR.OutputFile(path, header)
            out.writePixels({"R": r, "G": g, "B": b})
            out.close()
            return True
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"OpenEXR write failed: {e}")

        # Try OpenImageIO — write as half-float (16-bit)
        try:
            import OpenImageIO as oiio

            spec = oiio.ImageSpec(w, h, c, oiio.HALF)
            if colour_space:
                spec.attribute("oiio:ColorSpace", colour_space)

            out = oiio.ImageOutput.create(path)
            if out:
                out.open(path, spec)
                out.write_image(frame_f32)
                out.close()
                return True
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"OpenImageIO write failed: {e}")

        # Fallback: 8-bit PNG via PIL (lossy — install OpenEXR for proper output)
        try:
            from PIL import Image

            frame_u8 = (frame_f32 * 255.0 + 0.5).clip(0, 255).astype(np.uint8)
            png_path = path.replace(".exr", ".png")
            img = Image.fromarray(frame_u8, mode="RGB")
            img.save(png_path)
            logger.warning(
                f"Saved as 8-bit PNG (lossy): {png_path} — "
                f"install OpenEXR or OpenImageIO for half-float EXR output"
            )
            return True
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"PNG fallback failed: {e}")

        logger.error(
            f"No image writer available (tried OpenEXR, OpenImageIO, PIL). "
            f"Install one of: OpenEXR, OpenImageIO, Pillow"
        )
        return False
