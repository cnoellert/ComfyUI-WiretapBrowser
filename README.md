# ComfyUI-WiretapBrowser

**Browse and stream clip data from Autodesk Flame directly into ComfyUI.**

This custom node pack provides a visual tree browser for navigating Flame's clip library via the Wiretap SDK, loading frames as IMAGE tensors for AI-augmented VFX workflows, and writing processed results back to Flame — all without leaving ComfyUI.

[![Demo Video](https://img.shields.io/badge/▶_Watch_Demo-YouTube-red?style=for-the-badge&logo=youtube)](https://www.youtube.com/watch?v=33_jDm1xdbc)
> **⚠️ Python 3.11 required.** The Wiretap SDK and OpenColorIO binaries distributed with Flame are compiled against Python 3.11. Using Python 3.12+ will cause both to fail silently at startup. If you're using a conda environment, make sure to create it with `conda create -n comfyui python=3.11`.
---

## Nodes

### 🔥 Flame Wiretap Browser
Interactive tree browser that navigates the IFFFS hierarchy:

```
Server → Projects → Libraries → Reels → Clips
```

Click **"Browse Flame"** to open the modal dialog, navigate to a clip, and select it. The clip's node ID, resolution, frame count, FPS, and colour space are output for downstream use.

In **destination mode** (used by the Writer), you can browse to a reel and create new libraries or reels directly from the browser UI.

### 🔥 Flame Clip Loader
Reads frame data from a selected clip and converts the raw Wiretap RGB buffers into ComfyUI `IMAGE` tensors (`BHWC float32`). Supports all Flame bit depths:

| Bit Depth | Format | Bytes/Pixel |
|-----------|--------|-------------|
| 8-bit | Integer RGB | 3 |
| 10-bit | Integer (DPX-packed in 32-bit words) | 4 |
| 16-bit | Half-float (IEEE 754) | 6 |
| 32-bit | Float (IEEE 754) | 12 |

Also outputs `fps`, `colour_space`, and `clip_info` for downstream nodes.

### 🔥 Flame Clip Writer
Writes processed IMAGE tensors back to a Flame project as a new clip.

**Two modes:**
- **Wiretap mode** — Wire a destination reel from the Browser. The writer creates a new clip in Flame via CLI tools (`wiretap_create_clip` + `wiretap_rw_frame`), with configurable bit depth, frame rate, and colour space.
- **Disk-only mode** — When no destination is wired, saves frames as EXR files to a local directory.

**Writer inputs:**
| Input | Type | Description |
|-------|------|-------------|
| `bit_depth` | Dropdown | 16 (half-float, default), 32 (float), 10 (integer), 8 (integer) |
| `fps` | Float | Frame rate for the new clip (0 = use source fps) |
| `source_fps` | Float (optional wire) | Connect from Loader to preserve source frame rate |
| `colour_space` | String (optional wire) | Connect from OCIO Transform or Loader |
| `destination_node_id` | String (optional wire) | Connect from Browser in destination mode |

### 🔥 Flame OCIO Transform
Applies OpenColorIO colour space transforms using Flame's bundled OCIO configs.

- Source colour space auto-detected from the clip (via Wiretap's `colourSpace()`)
- Three-tier config resolution: projekt-forge project configs → Flame system configs → bundled ACES
- All processing in float32 — no precision loss through the transform
- Outputs the target colour space name for the Writer

Requires `opencolorio` Python package (`pip install opencolorio`).

### 🔥 Flame Clip Metadata
Extracts detailed clip metadata (resolution, bit depth, frame rate, colour space, format tag) as individual outputs for use in downstream logic.

### 🔥 Flame Server Info
Diagnostic node that shows:
- SDK detection status and module path
- CLI tool availability and paths
- Environment variable overrides
- Server connectivity and project listing

Use this node to troubleshoot SDK installation issues.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  ComfyUI Frontend (Browser)                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  wiretap_browser.js                                      │   │
│  │  - Tree browser modal UI                                 │   │
│  │  - Node creation (libraries, reels)                      │   │
│  │  - Calls /wiretap/* API endpoints                        │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │ HTTP/WebSocket                      │
├───────────────────────────┼─────────────────────────────────────┤
│  ComfyUI Backend (Python)  │                                     │
│  ┌─────────────────────────┴───────────────────────────────┐   │
│  │  wiretap_browser.py   (API routes + Browser node)        │   │
│  │  wiretap_loader.py    (ClipLoader + FrameWriter nodes)   │   │
│  │  wiretap_metadata.py  (Metadata extraction node)         │   │
│  │  ocio_transform.py    (OCIO colour space transforms)     │   │
│  │  frame_converter.py   (Raw RGB → torch tensor)           │   │
│  │  wiretap_connection.py (SDK wrapper + CLI tools + mock)  │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │ Wiretap Client API + CLI tools      │
├───────────────────────────┼─────────────────────────────────────┤
│  Flame Workstation         │                                     │
│  ┌─────────────────────────┴───────────────────────────────┐   │
│  │  ifffsWiretapServer (IFFFS) — port 7549                  │   │
│  │  - Projects, Libraries, Reels, Clips                     │   │
│  │  - Frame read/write via CLI tools                        │   │
│  │                                                          │   │
│  │  WiretapGateway (Gateway)                                │   │
│  │  - Filesystem media access                               │   │
│  │  - Format conversion for frame encoding                  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Installation

### 1. Clone into ComfyUI custom_nodes

```bash
cd /path/to/ComfyUI/custom_nodes/
git clone https://github.com/cnoellert/ComfyUI-WiretapBrowser.git
```

### 2. Install Python dependencies

```bash
pip install opencolorio   # Required for OCIO Transform node
```

### 3. Install the Wiretap SDK

The Wiretap SDK is available as a free standalone download from the [Autodesk Platform Services portal](https://aps.autodesk.com/developer/overview/wiretap). No Flame license is required on the ComfyUI machine.

#### Option A: ComfyUI runs on the Flame workstation

The SDK is auto-discovered from `/opt/Autodesk/`. No configuration needed.

#### Option B: ComfyUI runs on a separate machine

Copy the minimum SDK files from an existing Flame installation, or install the standalone SDK:

**Minimum files needed (~20 MB):**

| Component | Source Path | Purpose |
|-----------|-------------|---------|
| Python module | `/opt/Autodesk/python/<ver>/lib/python3.11/site-packages/adsk/` | Python API bindings |
| Core library | `/opt/Autodesk/lib64/<ver>/libwiretapClientAPI.dylib` (macOS) or `.so` (Linux) | Shared library |
| CLI tools | `/opt/Autodesk/wiretap/tools/current/wiretap_rw_frame`, `wiretap_create_clip`, `wiretap_create_node`, `wiretap_can_create_node` | Frame I/O and node creation |

Then set environment variables to tell the plugin where to find them:

```bash
export WIRETAP_SDK_PATH=/path/to/site-packages    # directory containing `adsk/` package
export WIRETAP_TOOLS_DIR=/path/to/tools            # directory containing CLI executables
export WIRETAP_LIB_DIR=/path/to/lib                # directory containing libwiretapClientAPI
```

> **Note:** The Python `.so` module is compiled for Python 3.11. If ComfyUI runs Python 3.12+, the Python API won't load, but CLI-based operations (frame read/write, clip creation) still work. The plugin handles this gracefully.

#### Option C: Development without Flame

The nodes run in **mock mode** automatically when the SDK is not found. Mock mode provides a simulated hierarchy and test frames for developing workflows without a Flame workstation.

### 4. Ensure network connectivity

The ComfyUI machine needs network access to the Flame workstation:
- **IFFFS server**: TCP port 7549 (default)
- **Gateway server**: TCP port 7500 (default)

### 5. Restart ComfyUI

Use the **🔥 Flame Server Info** node to verify SDK detection and connectivity.

---

## Usage

### Basic Workflow: Load → Process → Write Back

```
[Browser] → [Clip Loader] → [OCIO Transform] → [Your AI Node] → [OCIO Transform] → [Clip Writer]
 hostname     start_frame     source → linear    upscale/denoise   linear → source     destination
 clip_id      frame_count     colour_space        model_name       colour_space         clip_name
```

1. Add a **🔥 Flame Wiretap Browser** node, set hostname, click **"Browse Flame"**
2. Navigate to a clip and select it
3. Connect to a **🔥 Flame Clip Loader** — set start_frame and frame_count
4. (Optional) Add **🔥 Flame OCIO Transform** to convert colour space
5. Process through your AI nodes (upscale, denoise, style transfer, etc.)
6. (Optional) Add another OCIO Transform to convert back
7. Connect to a **🔥 Flame Clip Writer** with a destination reel from a second Browser

### Write-Back Workflow

To write results back into Flame:

1. Add a second **🔥 Flame Wiretap Browser** node
2. Browse to the destination **reel** (or create a new library/reel from the browser)
3. Wire the Browser's outputs to the Writer's destination inputs
4. Set **bit_depth** (16-bit half-float recommended), **clip_name**, and **fps**
5. Wire `colour_space` from the OCIO Transform or Loader to embed it in the clip

---

## Wiretap IFFFS Node Hierarchy

The Flame project structure as exposed by Wiretap:

```
/ (root)
└── /projects
    └── /<project_name>                    (PROJECT)
        ├── /workspace_id                  (WORKSPACE — read-only while open in Flame)
        │   └── /libraries_id             (LIBRARY_LIST)
        │       └── ...
        └── /shared_libs_id               (LIBRARY_LIST — always writable)
            └── /<library_id>              (LIBRARY)
                └── /<reel_id>             (REEL)
                    └── /<clip_id>         (CLIP)
                        ├── /hires         (HIRES — full res frames)
                        ├── /videotrack_01 (VIDEO_TRACK)
                        └── /slate         (SLATE)
```

> **Important:** Projects opened in Flame become read-only for structural changes (creating libraries/reels/clips) via Wiretap. Write to Shared Libraries reels that already exist, or create clips before opening the project in Flame. The IFFFS server uses the client's `umask` for write access — the plugin sets `umask(0)` automatically.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/wiretap/status` | GET | SDK availability, diagnostics, and CLI tool paths |
| `/wiretap/browse?hostname=&node_id=&server_type=` | GET | List children of a node |
| `/wiretap/clip_info?hostname=&node_id=` | GET | Get detailed clip format info |
| `/wiretap/create_node` | POST | Create a new node (library, reel, etc.) |

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WIRETAP_SDK_PATH` | Directory containing the `adsk` Python package | Auto-detected from `/opt/Autodesk/` |
| `WIRETAP_TOOLS_DIR` | Directory containing CLI tools (`wiretap_rw_frame`, etc.) | Auto-detected from `/opt/Autodesk/wiretap/tools/` |
| `WIRETAP_LIB_DIR` | Directory containing `libwiretapClientAPI.dylib`/`.so` | Auto-detected |

### Auto-detected SDK Paths

When no environment variables are set, the module searches:
1. `/opt/Autodesk/python/<version>/lib/python3.11/site-packages/` (newest first)
2. `/opt/Autodesk/wiretap/tools/current/python`
3. `/usr/discreet/wiretap/tools/current/python`
4. `/opt/Autodesk/.flamefamily_*/python`

---

## Known Limitations

- **Python version**: The Wiretap Python `.so` module is compiled for Python 3.11. ComfyUI typically runs 3.12+. The plugin uses CLI tools as a workaround for frame I/O, but the Python API (used for browsing) may not load on mismatched versions.
- **Boost.Python str/bytes mismatch**: `readFrame()` and `writeFrame()` via the Python API silently fail on Python 3 (immutable str issue). All frame I/O uses CLI tools (`wiretap_rw_frame`) instead.
- **12-bit clips**: Flame's playback engine crashes on 12-bit RGB clips. Reading 12-bit source clips works, but writing 12-bit is not offered.
- **Windows**: Wiretap SDK is not available on Windows. Use Linux or macOS.
- **Write access**: IFFFS uses the client's umask for write permissions. The plugin sets `umask(0)` automatically. Remote writes also require matching user/group on both machines.
- **Large clips**: Loading many frames at high bit-depth requires significant memory. Use `max_dimension` to resize, or load frame ranges.

---

## Development

### Running in Mock Mode

Without the Wiretap SDK, all nodes operate in mock mode:
- The browser shows a simulated project hierarchy
- The loader generates gradient test patterns
- The writer saves EXR files to disk only

This lets you develop and test workflows without a Flame workstation.

### Project Structure

```
ComfyUI-WiretapBrowser/
├── __init__.py              # ComfyUI registration + setup docs
├── wiretap_connection.py    # SDK discovery, CLI tools, connection management, mock mode
├── wiretap_browser.py       # Browser node + API routes
├── wiretap_loader.py        # Clip Loader + Frame Writer nodes
├── wiretap_metadata.py      # Clip Metadata extraction node
├── ocio_transform.py        # OCIO colour space transform node
├── frame_converter.py       # Raw RGB buffer → torch tensor conversion
├── js/
│   └── wiretap_browser.js   # Frontend tree browser UI
└── README.md
```

---

## Credits

- **Wiretap SDK** by Autodesk — [Developer docs](https://aps.autodesk.com/developer/overview/wiretap)
- Inspired by community work on [TimewarpML](https://forum.logik.tv/t/flame-machine-learning-timewarp-now-on-linux-and-mac/2038) by @talosh
- [Logik Forum Wiretap discussion](https://forum.logik.tv/t/wiretap-api-create-clip-help-needed/8439) for write workflow insights
- [CBS Digital Hiero-Wiretap](https://github.com/CBSDigital/Hiero-Wiretap) for Wiretap integration patterns
- [ShotGrid tk-flame](https://github.com/shotgunsoftware/tk-flame) for production Wiretap usage examples

## License

MIT
