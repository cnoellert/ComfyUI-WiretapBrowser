"""
OCIO Colour Space Transform Node

Applies OpenColorIO transforms to ComfyUI IMAGE tensors.

Config resolution (three tiers):
1. Auto-detect: Flame's local OCIO config at /opt/Autodesk/colour_mgmt/
2. Fallback: Bundled ACES studio config shipped with this node pack
3. User override: Manually select a config from the dropdown
"""

import os
import glob
import logging
import numpy as np
import torch
from typing import Optional, List, Tuple

logger = logging.getLogger("ComfyUI-WiretapBrowser")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

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

def _find_flame_configs() -> List[Tuple[str, str]]:
    """Discover Flame OCIO config files on the local filesystem.

    Scans /opt/Autodesk/colour_mgmt/configs/ for config.ocio files,
    excluding flame_internal_use directories (per projekt-forge).
    Returns list of (path, display_name) tuples sorted newest first.
    """
    base_dir = "/opt/Autodesk/colour_mgmt/configs"
    configs = []

    if not os.path.isdir(base_dir):
        return configs

    for root, dirs, files in os.walk(base_dir):
        if "flame_internal_use" in root:
            continue
        if "config.ocio" in files:
            config_path = os.path.join(root, "config.ocio")
            name = _read_ocio_name(config_path, os.path.basename(root))
            configs.append((config_path, name))

    configs.sort(key=lambda x: x[0], reverse=True)
    return configs


def _find_bundled_configs() -> List[Tuple[str, str]]:
    """Find OCIO configs shipped with this node pack."""
    configs_dir = os.path.join(_THIS_DIR, "ocio_configs")
    configs = []

    if not os.path.isdir(configs_dir):
        return configs

    for entry in sorted(os.listdir(configs_dir), reverse=True):
        config_path = os.path.join(configs_dir, entry, "config.ocio")
        if os.path.isfile(config_path):
            name = _read_ocio_name(config_path, entry)
            configs.append((config_path, f"{name} (bundled)"))

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
                if line.startswith("roles:") or line.startswith("displays:"):
                    break
    except Exception:
        pass
    return fallback


# ---------------------------------------------------------------------------
# Build available configs and colour space lists at import time
# ---------------------------------------------------------------------------

def _discover_all_configs() -> List[Tuple[str, str]]:
    """Build the ordered list of available OCIO configs.

    Priority:
    1. $OCIO environment variable
    2. Local Flame configs (auto-detected)
    3. Bundled configs shipped with this node pack
    """
    configs = []

    # 1. $OCIO environment variable
    env_path = os.environ.get("OCIO", "")
    if env_path and os.path.isfile(env_path):
        name = _read_ocio_name(env_path, "Environment ($OCIO)")
        configs.append((env_path, name))

    # 2. Local Flame configs
    configs.extend(_find_flame_configs())

    # 3. Bundled configs
    configs.extend(_find_bundled_configs())

    return configs


def _resolve_best_config(configs: List[Tuple[str, str]]) -> Optional[str]:
    """Pick the best config from the discovered list."""
    if not configs:
        return None

    # Prefer ACES 2.0 configs
    for path, name in configs:
        if "aces2" in name.lower() or "aces2" in path.lower():
            return path

    # Fall back to first available
    return configs[0][0]


def _load_colour_spaces(config_path: str) -> List[str]:
    """Load colour space names from an OCIO config file."""
    if not _ocio_available or not config_path:
        return []
    try:
        cfg = ocio.Config.CreateFromFile(config_path)
        return [cs.getName() for cs in cfg.getColorSpaces()]
    except Exception as e:
        logger.warning(f"Failed to load colour spaces from {config_path}: {e}")
        return []


# Discover configs and build dropdown lists
_all_configs = _discover_all_configs()
_default_config_path = _resolve_best_config(_all_configs)

# Config dropdown: "Auto" + discovered config display names
_config_choices = ["Auto (best available)"]
_config_path_map = {"Auto (best available)": None}  # None = use _default_config_path
for path, name in _all_configs:
    _config_choices.append(name)
    _config_path_map[name] = path

# Colour space dropdown from best config
_colour_space_names = _load_colour_spaces(_default_config_path) if _default_config_path else []

if _default_config_path:
    logger.info(
        f"OCIO default config: {_default_config_path} "
        f"({len(_colour_space_names)} colour spaces, "
        f"{len(_all_configs)} configs available)"
    )

if not _colour_space_names:
    _colour_space_names = [
        "ACEScg",
        "ACES2065-1",
        "Linear Rec.709 (sRGB)",
        "sRGB - Display",
        "Rec.1886 Rec.709 - Display",
        "Raw",
    ]


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class WiretapOCIOTransform:
    """
    Apply an OpenColorIO colour space transform to IMAGE tensors.

    Config resolution (three tiers):
    1. Auto-detect local Flame OCIO config
    2. Fall back to bundled ACES studio config
    3. User selects a specific config from the dropdown

    Source colour space can be wired from the Loader/Browser node
    or manually selected from the dropdown.
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
                "source_colour_space": (_colour_space_names, {
                    "default": _colour_space_names[0],
                    "forceInput": True,
                }),
                "target_colour_space": (_colour_space_names, {
                    "default": _colour_space_names[0],
                }),
            },
            "optional": {
                "ocio_config": (_config_choices, {
                    "default": _config_choices[0],
                }),
            },
        }

    def transform(
        self,
        images: torch.Tensor,
        source_colour_space: str,
        target_colour_space: str,
        ocio_config: str = "Auto (best available)",
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
            return (images, target_colour_space)

        # Resolve config path from dropdown selection
        config_path = _config_path_map.get(ocio_config)
        if config_path is None:
            config_path = _default_config_path
        if not config_path:
            logger.error("No OCIO config available")
            return (images, source_colour_space)

        try:
            cfg = ocio.Config.CreateFromFile(config_path)
        except Exception as e:
            logger.error(f"Failed to load OCIO config {config_path}: {e}")
            return (images, source_colour_space)

        # Validate colour space names
        available = set(cs.getName() for cs in cfg.getColorSpaces())
        if source_colour_space not in available:
            logger.error(
                f"Source colour space '{source_colour_space}' not found "
                f"in {os.path.basename(os.path.dirname(config_path))} config"
            )
            return (images, source_colour_space)

        if target_colour_space not in available:
            logger.error(
                f"Target colour space '{target_colour_space}' not found "
                f"in {os.path.basename(os.path.dirname(config_path))} config"
            )
            return (images, source_colour_space)

        # Build processor
        try:
            processor = cfg.getProcessor(
                source_colour_space, target_colour_space
            )
            cpu = processor.getDefaultCPUProcessor()
        except Exception as e:
            logger.error(
                f"OCIO processor failed "
                f"({source_colour_space} → {target_colour_space}): {e}"
            )
            return (images, source_colour_space)

        logger.info(
            f"OCIO: {source_colour_space} → {target_colour_space} "
            f"({os.path.basename(os.path.dirname(config_path))})"
        )

        # Apply transform to each frame
        result_frames = []
        for i in range(images.shape[0]):
            frame = images[i].cpu().numpy().copy()
            img = ocio.PackedImageDesc(frame, frame.shape[1], frame.shape[0], 3)
            cpu.apply(img)
            result_frames.append(torch.from_numpy(frame))

        result = torch.stack(result_frames, dim=0)
        return (result, target_colour_space)
