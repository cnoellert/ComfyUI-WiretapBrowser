"""
Frame Converter for Wiretap Raw RGB Buffers

Converts raw RGB frame data from the Wiretap SDK into PyTorch tensors
compatible with ComfyUI's IMAGE type (BHWC float32, 0.0-1.0 range).

Wiretap raw RGB format details:
- 8-bit:  3 bytes/pixel, packed contiguously
- 10-bit: 4 bytes/pixel, filled to 32-bit word boundaries (2 bits padding)
- 12-bit packed: 4.5 bytes/pixel, Autodesk-specific packed format
- 12-bit unpacked: 6 bytes/pixel, filled to 16-bit word boundaries
- 16-bit float: 6 bytes/pixel (half-precision IEEE 754)
- 32-bit float: 12 bytes/pixel (single-precision IEEE 754)
"""

import numpy as np
import torch
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("ComfyUI-WiretapBrowser")


def raw_rgb_to_tensor(
    raw_bytes: bytes,
    format_info: Dict[str, Any],
) -> torch.Tensor:
    """
    Convert a Wiretap raw RGB frame buffer to a ComfyUI IMAGE tensor.

    Args:
        raw_bytes: Raw frame bytes from WireTapNodeHandle.readFrame()
        format_info: Dict with width, height, bit_depth, num_channels, etc.

    Returns:
        torch.Tensor of shape (1, H, W, 3) in float32, range [0.0, 1.0]
    """
    width = format_info["width"]
    height = format_info["height"]
    bit_depth = format_info["bit_depth"]
    num_channels = format_info.get("num_channels", 3)
    format_tag = format_info.get("format_tag", "")
    is_float = "float" in format_tag.lower()

    logger.debug(
        f"Decoding frame: {width}x{height} bit_depth={bit_depth} "
        f"tag={format_tag} float={is_float}"
    )

    arr = np.frombuffer(raw_bytes, dtype=np.uint8)

    if bit_depth == 8:
        image = _decode_8bit(arr, width, height)
    elif bit_depth == 10:
        image = _decode_10bit(arr, width, height)
    elif bit_depth == 12:
        bpp = format_info.get("bits_per_pixel", 36)
        if bpp == 36:
            image = _decode_12bit_packed(arr, width, height)
        else:
            image = _decode_12bit_unpacked(arr, width, height)
    elif bit_depth == 16:
        if is_float:
            image = _decode_16bit_float(arr, width, height)
        else:
            image = _decode_16bit_int(arr, width, height)
    elif bit_depth == 32:
        if is_float:
            image = _decode_32bit_float(arr, width, height)
        else:
            image = _decode_32bit_int(arr, width, height)
    else:
        logger.warning(
            f"Unknown bit depth {bit_depth}, attempting 8-bit decode"
        )
        image = _decode_8bit(arr, width, height)

    # Wiretap stores frames bottom-to-top (like BMP/OpenGL); flip to top-down.
    image = np.flipud(image)

    # Ensure correct shape: (H, W, 3) float32 in [0, 1]
    if image.ndim == 2:
        image = np.stack([image] * 3, axis=-1)
    elif image.shape[-1] == 1:
        image = np.repeat(image, 3, axis=-1)
    elif image.shape[-1] > 3:
        image = image[:, :, :3]

    # Convert to torch tensor with batch dimension: (1, H, W, 3)
    tensor = torch.from_numpy(image.astype(np.float32))
    if tensor.ndim == 3:
        tensor = tensor.unsqueeze(0)

    return tensor


def _decode_8bit(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 8-bit RGB: 3 bytes/pixel, packed.
    Scan-line order, left to right. Vertical flip handled by caller.
    """
    bytes_per_pixel = 3
    # Calculate scan line padding to 32-bit boundary
    bits_per_line = width * bytes_per_pixel * 8
    padding_bits = (32 - (bits_per_line % 32)) % 32
    bytes_per_line = (bits_per_line + padding_bits) // 8

    image = np.zeros((height, width, 3), dtype=np.float32)

    for y in range(height):
        line_start = y * bytes_per_line
        for x in range(width):
            px_start = line_start + x * bytes_per_pixel
            if px_start + 2 < len(arr):
                # Wiretap 8-bit: R, G, B order (based on SDK docs)
                image[y, x, 0] = arr[px_start] / 255.0      # R
                image[y, x, 1] = arr[px_start + 1] / 255.0   # G
                image[y, x, 2] = arr[px_start + 2] / 255.0   # B

    return image


def _decode_8bit_fast(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """Fast vectorized 8-bit decode (no scan line padding handling)."""
    expected = height * width * 3
    if len(arr) >= expected:
        image = arr[:expected].reshape(height, width, 3).astype(np.float32) / 255.0
        return image
    else:
        return _decode_8bit(arr, width, height)


def _decode_10bit(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 10-bit RGB: 4 bytes (32 bits) per pixel, filled.
    Layout per 32-bit word: [2 pad bits][10-bit B][10-bit G][10-bit R]
    MSB first: bits 31-30 = pad, 29-20 = B, 19-10 = G, 9-0 = R
    """
    bytes_per_pixel = 4
    bits_per_line = width * bytes_per_pixel * 8
    padding_bits = (32 - (bits_per_line % 32)) % 32
    bytes_per_line_padded = (bits_per_line + padding_bits) // 8

    # Try fast path: interpret as uint32 array
    try:
        # Each pixel is a 32-bit word
        total_pixels = height * width
        if len(arr) >= total_pixels * 4:
            words = np.frombuffer(arr[:total_pixels * 4].tobytes(), dtype=np.uint32)
            words = words.reshape(height, width)

            r = (words & 0x3FF).astype(np.float32) / 1023.0
            g = ((words >> 10) & 0x3FF).astype(np.float32) / 1023.0
            b = ((words >> 20) & 0x3FF).astype(np.float32) / 1023.0

            image = np.stack([r, g, b], axis=-1)
            return image
    except Exception as e:
        logger.warning(f"Fast 10-bit decode failed, using slow path: {e}")

    # Slow fallback
    image = np.zeros((height, width, 3), dtype=np.float32)
    for y in range(height):
        line_start = y * bytes_per_line_padded
        for x in range(width):
            px_start = line_start + x * 4
            if px_start + 3 < len(arr):
                word = (
                    arr[px_start]
                    | (arr[px_start + 1] << 8)
                    | (arr[px_start + 2] << 16)
                    | (arr[px_start + 3] << 24)
                )
                image[y, x, 0] = (word & 0x3FF) / 1023.0
                image[y, x, 1] = ((word >> 10) & 0x3FF) / 1023.0
                image[y, x, 2] = ((word >> 20) & 0x3FF) / 1023.0

    return image


def _decode_12bit_packed(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 12-bit packed RGB: 4.5 bytes (36 bits) per pixel.
    This is an Autodesk-specific format. Two pixels occupy 9 bytes.
    """
    image = np.zeros((height, width, 3), dtype=np.float32)
    max_val = 4095.0

    # Process 2 pixels at a time (9 bytes)
    bytes_per_line = (width * 36 + 31) // 32 * 4  # padded to 32-bit

    for y in range(height):
        line_start = y * bytes_per_line
        for x in range(0, width, 2):
            base = line_start + (x // 2) * 9

            if base + 8 >= len(arr):
                break

            # First pixel: bytes 0-4
            r0 = arr[base + 0] | ((arr[base + 1] & 0x0F) << 8)
            g0 = (arr[base + 1] >> 4) | (arr[base + 2] << 4)
            b0 = arr[base + 3] | ((arr[base + 4] & 0x0F) << 8)

            image[y, x, 0] = r0 / max_val
            image[y, x, 1] = g0 / max_val
            image[y, x, 2] = b0 / max_val

            # Second pixel: bytes 4-8
            if x + 1 < width:
                r1 = (arr[base + 4] >> 4) | (arr[base + 5] << 4)
                g1 = arr[base + 6] | ((arr[base + 7] & 0x0F) << 8)
                b1 = (arr[base + 7] >> 4) | (arr[base + 8] << 4)

                image[y, x + 1, 0] = r1 / max_val
                image[y, x + 1, 1] = g1 / max_val
                image[y, x + 1, 2] = b1 / max_val

    return image


def _decode_12bit_unpacked(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 12-bit unpacked/filled RGB: 6 bytes (48 bits) per pixel.
    Each component is stored in 16 bits with 4 bits of padding.
    Layout per 16-bit word: [4 pad bits][12-bit value]
    """
    try:
        total_pixels = height * width
        if len(arr) >= total_pixels * 6:
            words = np.frombuffer(
                arr[:total_pixels * 6].tobytes(), dtype=np.uint16
            )
            words = words.reshape(height, width, 3)
            image = (words & 0x0FFF).astype(np.float32) / 4095.0
            return image
    except Exception as e:
        logger.warning(f"Fast 12-bit unpacked decode failed: {e}")

    # Slow fallback
    image = np.zeros((height, width, 3), dtype=np.float32)
    for y in range(height):
        for x in range(width):
            base = (y * width + x) * 6
            for c in range(3):
                offset = base + c * 2
                if offset + 1 < len(arr):
                    val = arr[offset] | (arr[offset + 1] << 8)
                    image[y, x, c] = (val & 0x0FFF) / 4095.0
    return image


def _decode_16bit_int(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 16-bit unsigned integer RGB: 6 bytes per pixel.
    Format tag: rgb or rgb_le (little-endian uint16 per channel).
    """
    try:
        total = height * width * 3 * 2
        if len(arr) >= total:
            words = np.frombuffer(arr[:total].tobytes(), dtype=np.uint16)
            image = words.reshape(height, width, 3).astype(np.float32) / 65535.0
            return image
    except Exception as e:
        logger.warning(f"16-bit int decode failed: {e}")

    return np.zeros((height, width, 3), dtype=np.float32)


def _decode_32bit_int(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 32-bit unsigned integer RGB: 12 bytes per pixel.
    """
    try:
        total = height * width * 3 * 4
        if len(arr) >= total:
            words = np.frombuffer(arr[:total].tobytes(), dtype=np.uint32)
            image = words.reshape(height, width, 3).astype(np.float64) / 4294967295.0
            return image.astype(np.float32)
    except Exception as e:
        logger.warning(f"32-bit int decode failed: {e}")

    return np.zeros((height, width, 3), dtype=np.float32)


def _decode_16bit_float(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 16-bit float (half-precision) RGB: 6 bytes per pixel.
    """
    try:
        total = height * width * 3 * 2  # 2 bytes per float16
        if len(arr) >= total:
            floats = np.frombuffer(arr[:total].tobytes(), dtype=np.float16)
            image = floats.reshape(height, width, 3).astype(np.float32)
            # Clamp to [0, 1] for ComfyUI
            image = np.clip(image, 0.0, 1.0)
            return image
    except Exception as e:
        logger.warning(f"16-bit float decode failed: {e}")

    return np.zeros((height, width, 3), dtype=np.float32)


def _decode_32bit_float(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Decode 32-bit float (single-precision) RGB: 12 bytes per pixel.
    """
    try:
        total = height * width * 3 * 4  # 4 bytes per float32
        if len(arr) >= total:
            floats = np.frombuffer(arr[:total].tobytes(), dtype=np.float32)
            image = floats.reshape(height, width, 3)
            image = np.clip(image, 0.0, 1.0)
            return image
    except Exception as e:
        logger.warning(f"32-bit float decode failed: {e}")

    return np.zeros((height, width, 3), dtype=np.float32)


def batch_frames_to_tensor(
    frames: list,
    format_info: Dict[str, Any],
) -> torch.Tensor:
    """
    Convert a list of raw frame buffers to a batched ComfyUI IMAGE tensor.

    Args:
        frames: List of raw byte buffers, one per frame.
        format_info: Shared format dict for all frames.

    Returns:
        torch.Tensor of shape (N, H, W, 3) in float32, range [0.0, 1.0]
    """
    tensors = []
    for raw_bytes in frames:
        t = raw_rgb_to_tensor(raw_bytes, format_info)
        tensors.append(t)

    if tensors:
        return torch.cat(tensors, dim=0)
    else:
        w = format_info.get("width", 64)
        h = format_info.get("height", 64)
        return torch.zeros((1, h, w, 3), dtype=torch.float32)
