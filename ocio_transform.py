"""
OCIO Colour Space Transform Node

Applies OpenColorIO transforms to ComfyUI IMAGE tensors using
Flame's bundled OCIO config or a user-specified config file.
"""

import os
import glob
import logging
import numpy as np
import torch
from typing import Optional, List, Tuple

logger = logging.getLogger("ComfyUI-WiretapBrowser")

# ---------------------------------------------------------------------------
# PyOpenColorIO import
# ---------------------------------------------------------------------------

_ocio_available = False
_ocio_import_error = None
ocio = None

try:
    import PyOpenColorIO as ocio
    _ocio_available = True
    logger.info(f"PyOpenColorIO loaded: {ocio.__version__}")
except ImportError as e:
    _ocio_import_error = str(e)
    logger.warning(
        f"PyOpenColorIO not available: {e}. "
        f"OCIO Transform node will pass through unchanged."
    )

# ---------------------------------------------------------------------------
# OCIO config discovery (follows projekt-forge conventions)
# ---------------------------------------------------------------------------

def _find_ocio_configs() -> List[Tuple[str, str]]:
    """Discover all Flame OCIO config files.

    Scans /opt/Autodesk/colour_mgmt/configs/ for config.ocio files,
    excluding flame_internal_use directories (following projekt-forge
    conventions). Returns list of (path, name) tuples sorted newest first.
    """
    base_dir = "/opt/Autodesk/colour_mgmt/configs"
    configs = []

    if not os.path.isdir(base_dir):
        return configs

    for root, dirs, files in os.walk(base_dir):
        # Skip internal-only configs (not user-facing)
        if "flame_internal_use" in root:
            continue
        if "config.ocio" in files:
            config_path = os.path.join(root, "config.ocio")
            name = _read_ocio_name(config_path, os.path.basename(root))
            configs.append((config_path, name))

    # Sort by path descending so newest versioned configs come first
    configs.sort(key=lambda x: x[0], reverse=True)
    return configs


def _read_ocio_name(config_path: str, fallback: str) -> str:
    """Read the name field from an OCIO config file."""
    try:
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("name:"):
                    name = line[5:].strip().strip("'\"")
                    if name:
                        return name
                if line.startswith("description:"):
                    desc = line[12:].strip().strip("'\"")
                    if desc:
                        return desc
                # Stop after the header section
                if line.startswith("roles:") or line.startswith("displays:"):
                    break
    except Exception:
        pass
    return fallback


def _find_ocio_config() -> Optional[str]:
    """Auto-discover the best Flame OCIO config file.

    Search order:
    1. $OCIO environment variable
    2. Flame versioned configs (newest first, preferring aces2.0)
    3. Any other config.ocio under colour_mgmt/configs/
    """
    # 1. Environment variable
    env_path = os.environ.get("OCIO", "")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2. Discover from Flame's colour_mgmt directory
    configs = _find_ocio_configs()
    if configs:
        # Prefer ACES 2.0 configs
        for path, name in configs:
            if "aces2" in name.lower() or "aces2" in path.lower():
                return path
        # Fall back to first available
        return configs[0][0]

    return None


# ---------------------------------------------------------------------------
# Build colour space list from discovered config at import time
# ---------------------------------------------------------------------------

_default_config_path = _find_ocio_config()
_colour_space_names: List[str] = []

if _ocio_available and _default_config_path:
    try:
        _cfg = ocio.Config.CreateFromFile(_default_config_path)
        _colour_space_names = [cs.getName() for cs in _cfg.getColorSpaces()]
        logger.info(
            f"OCIO config loaded: {_default_config_path} "
            f"({len(_colour_space_names)} colour spaces)"
        )
        del _cfg
    except Exception as e:
        logger.warning(f"Failed to load OCIO config for colour space list: {e}")

if not _colour_space_names:
    # Fallback list of common colour spaces
    _colour_space_names = [
        "sRGB - Display",
        "Rec.1886 Rec.709 - Display",
        "ACEScg",
        "ACES2065-1",
        "Linear Rec.709 (sRGB)",
        "Raw",
    ]


class WiretapOCIOTransform:
    """
    Apply an OpenColorIO colour space transform to IMAGE tensors.

    Converts frames from the source colour space (as reported by the
    Wiretap clip) to a target colour space using Flame's bundled OCIO
    config or a user-specified config.

    If PyOpenColorIO is not available, frames pass through unchanged.
    """

    CATEGORY = "Wiretap/Flame"
    FUNCTION = "transform"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "colour_space")
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "source_colour_space": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "forceInput": True,
                    "description": "Source colour space (from Wiretap clip)",
                }),
                "target_colour_space": (_colour_space_names, {
                    "default": _colour_space_names[0],
                }),
            },
        }

    def transform(
        self,
        images: torch.Tensor,
        source_colour_space: str,
        target_colour_space: str,
    ):
        if not _ocio_available:
            logger.warning(
                "PyOpenColorIO not available — passing through unchanged"
            )
            return (images, source_colour_space)

        if not source_colour_space:
            logger.warning(
                "No source colour space provided — passing through unchanged"
            )
            return (images, target_colour_space)

        if source_colour_space == target_colour_space:
            logger.debug(
                f"Source and target are the same ({source_colour_space}) "
                f"— no transform needed"
            )
            return (images, target_colour_space)

        # Resolve OCIO config (auto-discovered at import, or $OCIO env var)
        config_path = _default_config_path
        if not config_path:
            logger.error(
                "No OCIO config found. Set $OCIO or provide ocio_config path. "
                "Searched /opt/Autodesk/colour_mgmt/configs/"
            )
            return (images, source_colour_space)

        logger.info(f"Using OCIO config: {config_path}")

        try:
            cfg = ocio.Config.CreateFromFile(config_path)
        except Exception as e:
            logger.error(f"Failed to load OCIO config {config_path}: {e}")
            return (images, source_colour_space)

        # Validate colour space names exist in the config
        available = [cs.getName() for cs in cfg.getColorSpaces()]
        if source_colour_space not in available:
            logger.error(
                f"Source colour space '{source_colour_space}' not found "
                f"in OCIO config. Available: {available[:20]}..."
            )
            return (images, source_colour_space)

        if target_colour_space not in available:
            logger.error(
                f"Target colour space '{target_colour_space}' not found "
                f"in OCIO config. Available: {available[:20]}..."
            )
            return (images, source_colour_space)

        # Build the processor
        try:
            processor = cfg.getProcessor(
                source_colour_space, target_colour_space
            )
            cpu = processor.getDefaultCPUProcessor()
        except Exception as e:
            logger.error(
                f"Failed to create OCIO processor "
                f"({source_colour_space} → {target_colour_space}): {e}"
            )
            return (images, source_colour_space)

        logger.info(
            f"OCIO transform: {source_colour_space} → {target_colour_space} "
            f"(config: {os.path.basename(config_path)})"
        )

        # Apply transform to each frame in the batch
        # ComfyUI IMAGE: (B, H, W, 3) float32
        result_frames = []
        for i in range(images.shape[0]):
            frame = images[i].cpu().numpy().copy()  # (H, W, 3) float32
            h, w, c = frame.shape

            # OCIO expects (data, width, height, numChannels) — all positional
            img = ocio.PackedImageDesc(frame, w, h, 3)
            cpu.apply(img)

            result_frames.append(torch.from_numpy(frame))

        result = torch.stack(result_frames, dim=0)
        return (result, target_colour_space)
