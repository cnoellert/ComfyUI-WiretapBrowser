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


def _detect_image_file(raw_bytes: bytes) -> Optional[str]:
    """Check if raw_bytes starts with a known image file magic number."""
    if len(raw_bytes) < 8:
        return None
    magic = raw_bytes[:4]
    # DPX: "SDPX" (big-endian) or "XPDS" (little-endian)
    if magic in (b"SDPX", b"XPDS"):
        return "dpx"
    # OpenEXR magic: 0x762f3101
    if raw_bytes[:4] == b"\x76\x2f\x31\x01":
        return "exr"
    # TIFF: "II" or "MM" followed by 42
    if raw_bytes[:2] in (b"II", b"MM"):
        return "tiff"
    # PNG
    if raw_bytes[:4] == b"\x89PNG":
        return "png"
    return None


def _decode_image_file(raw_bytes: bytes, file_format: str) -> Optional[np.ndarray]:
    """Decode an image file buffer using OpenImageIO or PIL, returning HWC float32."""
    import tempfile
    ext = {"dpx": ".dpx", "exr": ".exr", "tiff": ".tif", "png": ".png"}[file_format]

    # Write to temp file so OIIO/PIL can open it
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        # Try OpenImageIO first
        try:
            import OpenImageIO as oiio
            inp = oiio.ImageInput.open(tmp_path)
            if inp:
                spec = inp.spec()
                image = np.zeros((spec.height, spec.width, spec.nchannels), dtype=np.float32)
                inp.read_image(oiio.FLOAT, image)
                inp.close()
                logger.info(
                    f"Decoded {file_format.upper()} via OIIO: "
                    f"{spec.width}x{spec.height}x{spec.nchannels}"
                )
                return image[:, :, :3] if spec.nchannels > 3 else image
        except ImportError:
            pass

        # Try OpenEXR for EXR files
        if file_format == "exr":
            try:
                import OpenEXR
                import Imath
                exr = OpenEXR.InputFile(tmp_path)
                header = exr.header()
                dw = header["dataWindow"]
                w = dw.max.x - dw.min.x + 1
                h = dw.max.y - dw.min.y + 1
                pt = Imath.PixelType(Imath.PixelType.FLOAT)
                r = np.frombuffer(exr.channel("R", pt), dtype=np.float32).reshape(h, w)
                g = np.frombuffer(exr.channel("G", pt), dtype=np.float32).reshape(h, w)
                b = np.frombuffer(exr.channel("B", pt), dtype=np.float32).reshape(h, w)
                image = np.stack([r, g, b], axis=-1)
                logger.info(f"Decoded EXR via OpenEXR: {w}x{h}")
                return image
            except ImportError:
                pass

        # PIL fallback
        try:
            from PIL import Image
            img = Image.open(tmp_path).convert("RGB")
            image = np.array(img).astype(np.float32) / 255.0
            logger.info(f"Decoded {file_format.upper()} via PIL: {img.size}")
            return image
        except ImportError:
            pass

        logger.error(
            f"Cannot decode {file_format.upper()} file — "
            f"install OpenImageIO, OpenEXR, or Pillow"
        )
        return None
    finally:
        import os
        os.unlink(tmp_path)


def raw_rgb_to_tensor(
    raw_bytes: bytes,
    format_info: Dict[str, Any],
) -> torch.Tensor:
    """
    Convert a Wiretap raw RGB frame buffer to a ComfyUI IMAGE tensor.

    If the buffer is actually an image file (DPX, EXR, etc.) written by
    wiretap_rw_frame, it will be detected and decoded via OIIO/OpenEXR.

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

    logger.debug(
        f"Decoding frame: {width}x{height} bit_depth={bit_depth} "
        f"tag={format_tag}"
    )

    # Check if the CLI tool wrote an image file (DPX, EXR, etc.)
    # instead of raw RGB bytes
    file_format = _detect_image_file(raw_bytes)
    if file_format:
        logger.info(
            f"Detected {file_format.upper()} image file in frame buffer "
            f"— decoding via image library"
        )
        image = _decode_image_file(raw_bytes, file_format)
        if image is not None:
            image = np.clip(image, 0.0, 1.0)
            tensor = torch.from_numpy(image.astype(np.float32))
            if tensor.ndim == 3:
                tensor = tensor.unsqueeze(0)
            return tensor
        logger.warning(
            f"Image file decode failed, falling back to raw RGB decode"
        )

    arr = np.frombuffer(raw_bytes, dtype=np.uint8)

    # Flame bit-depth conventions:
    #   8, 10, 12  — integer formats
    #   16, 32     — always floating point (half / single precision)
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
        image = _decode_16bit_float(arr, width, height)
    elif bit_depth == 32:
        image = _decode_32bit_float(arr, width, height)
    else:
        logger.warning(
            f"Unknown bit depth {bit_depth}, attempting 8-bit decode"
        )
        image = _decode_8bit(arr, width, height)

    # Wiretap raw buffers are bottom-to-top (like BMP/OpenGL); flip to top-down.
    # Direct file reads are already top-down — skip the flip.
    if not format_info.get("_direct_read", False):
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
    Decode 10-bit RGB: 4 bytes (32 bits) per pixel, big-endian.
    DPX method A layout per 32-bit word (MSB → LSB):
        [10-bit R][10-bit G][10-bit B][2 pad bits]
        bits 31-22 = R, bits 21-12 = G, bits 11-2 = B, bits 1-0 = pad
    """
    # Fast path: interpret as big-endian uint32 array
    try:
        total_pixels = height * width
        if len(arr) >= total_pixels * 4:
            words = np.frombuffer(
                arr[:total_pixels * 4].tobytes(), dtype=">u4"
            )
            words = words.reshape(height, width)

            r = ((words >> 22) & 0x3FF).astype(np.float32) / 1023.0
            g = ((words >> 12) & 0x3FF).astype(np.float32) / 1023.0
            b = ((words >> 2) & 0x3FF).astype(np.float32) / 1023.0

            image = np.stack([r, g, b], axis=-1)
            return image
    except Exception as e:
        logger.warning(f"Fast 10-bit decode failed, using slow path: {e}")

    # Slow fallback (big-endian word construction)
    bytes_per_pixel = 4
    bytes_per_line = width * bytes_per_pixel
    image = np.zeros((height, width, 3), dtype=np.float32)
    for y in range(height):
        line_start = y * bytes_per_line
        for x in range(width):
            px_start = line_start + x * 4
            if px_start + 3 < len(arr):
                word = (
                    (arr[px_start] << 24)
                    | (arr[px_start + 1] << 16)
                    | (arr[px_start + 2] << 8)
                    | arr[px_start + 3]
                )
                image[y, x, 0] = ((word >> 22) & 0x3FF) / 1023.0
                image[y, x, 1] = ((word >> 12) & 0x3FF) / 1023.0
                image[y, x, 2] = ((word >> 2) & 0x3FF) / 1023.0

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
