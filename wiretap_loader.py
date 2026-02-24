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
    Write IMAGE tensor frames as an EXR image sequence to disk.

    Saves processed frames as 32-bit float EXR files that can be
    imported into Flame via MediaHub. Direct Wiretap IFFFS writes
    are blocked while Flame has the project open (database lock),
    so writing to disk is the reliable approach.

    The output_path string can be used to locate the sequence for
    import into Flame or other applications.
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
                    "description": "Directory to write the EXR sequence into",
                }),
                "clip_name": ("STRING", {
                    "default": "comfyui_output",
                    "multiline": False,
                    "description": "Base name for the image sequence",
                }),
                "start_frame": ("INT", {
                    "default": 1001,
                    "min": 0,
                    "max": 9999999,
                    "step": 1,
                    "description": "Starting frame number for the sequence",
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
        colour_space: str = "",
    ):
        """Save frames as an EXR image sequence to disk."""
        import os

        if not output_directory:
            logger.error("No output_directory provided")
            return ("",)

        # Create the output directory
        seq_dir = os.path.join(output_directory, clip_name)
        os.makedirs(seq_dir, exist_ok=True)

        batch_size, h, w, c = images.shape
        logger.info(
            f"Writing {batch_size} frames ({w}x{h}) to {seq_dir}"
        )

        written = 0
        for i in range(batch_size):
            frame_num = start_frame + i
            frame = images[i].cpu().numpy()
            file_path = os.path.join(
                seq_dir, f"{clip_name}.{frame_num:07d}.exr"
            )
            if self._save_frame_exr(frame, file_path, colour_space):
                written += 1

        logger.info(
            f"Wrote {written}/{batch_size} frames to {seq_dir}"
        )

        return (seq_dir,)

    def _save_frame_exr(
        self, frame_np: np.ndarray, path: str, colour_space: str = ""
    ) -> bool:
        """Save a single frame as a 32-bit float EXR file.

        Tries OpenEXR first, then OpenImageIO, then falls back to
        writing a 16-bit PNG via numpy/torch.
        """
        frame_f32 = np.clip(frame_np, 0.0, 1.0).astype(np.float32)

        # Try OpenEXR
        try:
            import OpenEXR
            import Imath

            h, w, c = frame_f32.shape
            header = OpenEXR.Header(w, h)
            float_chan = Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT))
            header["channels"] = {
                "R": float_chan, "G": float_chan, "B": float_chan
            }
            if colour_space:
                header["chromaticities"] = colour_space

            r = frame_f32[:, :, 0].tobytes()
            g = frame_f32[:, :, 1].tobytes()
            b = frame_f32[:, :, 2].tobytes()

            out = OpenEXR.OutputFile(path, header)
            out.writePixels({"R": r, "G": g, "B": b})
            out.close()
            return True
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"OpenEXR write failed: {e}")

        # Try OpenImageIO
        try:
            import OpenImageIO as oiio

            h, w, c = frame_f32.shape
            spec = oiio.ImageSpec(w, h, c, oiio.FLOAT)
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

        # Fallback: save as 16-bit PNG (not ideal but universally supported)
        try:
            from PIL import Image

            frame_u16 = (frame_f32 * 65535).astype(np.uint16)
            png_path = path.replace(".exr", ".png")
            img = Image.fromarray(frame_u16)
            img.save(png_path)
            logger.info(f"Saved as PNG fallback: {png_path}")
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
