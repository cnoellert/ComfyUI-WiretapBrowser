# ComfyUI-WiretapBrowser

**Browse and stream clip data from Autodesk Flame directly into ComfyUI.**

This custom node pack provides a visual tree browser for navigating Flame's clip library via the Wiretap SDK, loading frames as IMAGE tensors for AI-augmented VFX workflows, and writing results back to Flame.

---

## Nodes

### 🔥 Flame Wiretap Browser
Interactive tree browser that navigates the IFFFS hierarchy:

```
Server → Projects → Libraries → Reels → Clips
```

Click **"Browse Flame"** to open the modal dialog, navigate to a clip, and select it. The clip's node ID, resolution, frame count, and FPS are output for downstream use.

### 🔥 Flame Clip Loader
Reads frame data from a selected clip and converts the raw Wiretap RGB buffers into ComfyUI `IMAGE` tensors (`BHWC float32`). Supports all Flame bit depths:

| Bit Depth | Format | Bytes/Pixel |
|-----------|--------|-------------|
| 8-bit | Integer | 3 |
| 10-bit | Integer (filled to 32-bit) | 4 |
| 12-bit packed | Integer (Autodesk-specific) | 4.5 |
| 12-bit unpacked | Integer (filled to 16-bit) | 6 |
| 16-bit | Half-float (IEEE 754) | 6 |
| 32-bit | Float (IEEE 754) | 12 |

### 🔥 Flame Clip Writer
Writes processed IMAGE tensors back to a Flame library as a new clip. Uses the Gateway server workaround (temp files → Gateway read → IFFFS write), which is the community-proven approach used by TimewarpML.

### 🔥 Flame Server Info
Diagnostic node that checks Wiretap SDK availability, tests connectivity, and lists available projects.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  ComfyUI Frontend (Browser)                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  wiretap_browser.js                                      │   │
│  │  - Tree browser modal UI                                 │   │
│  │  - Calls /wiretap/browse and /wiretap/clip_info APIs     │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │ HTTP/WebSocket                      │
├───────────────────────────┼─────────────────────────────────────┤
│  ComfyUI Backend (Python)  │                                     │
│  ┌─────────────────────────┴───────────────────────────────┐   │
│  │  wiretap_browser.py (API routes + Browser node)          │   │
│  │  wiretap_loader.py  (ClipLoader + FrameWriter nodes)     │   │
│  │  frame_converter.py (Raw RGB → torch tensor)             │   │
│  │  wiretap_connection.py (SDK wrapper + mock mode)         │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │ Wiretap Client API                  │
├───────────────────────────┼─────────────────────────────────────┤
│  Flame Workstation         │                                     │
│  ┌─────────────────────────┴───────────────────────────────┐   │
│  │  ifffsWiretapServer (IFFFS)                              │   │
│  │  - Projects, Libraries, Reels, Clips                     │   │
│  │  - Frame read/write                                      │   │
│  │                                                          │   │
│  │  WiretapGateway (Gateway)                                │   │
│  │  - Filesystem media access                               │   │
│  │  - Format conversion to raw RGB                          │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Installation

### 1. Clone into ComfyUI custom_nodes

```bash
cd /path/to/ComfyUI/custom_nodes/
git clone https://github.com/your-org/ComfyUI-WiretapBrowser.git
```

### 2. Install the Wiretap SDK

The Wiretap SDK ships with Autodesk Flame and is also available as a standalone download from the [Autodesk Developer Network](https://aps.autodesk.com/developer/overview/wiretap).

**Option A: Flame is on the same machine**
The SDK is typically at `/opt/Autodesk/wiretap/tools/current/python`. The node auto-detects this path.

**Option B: Standalone SDK**
Download from ADN, install, and set the environment variable:
```bash
export WIRETAP_SDK_PATH=/path/to/wiretap/python
```

**Option C: Development without Flame**
The nodes run in **mock mode** automatically when the Wiretap SDK is not found. Mock mode provides a simulated hierarchy and test frames, allowing you to develop workflows without a Flame workstation.

### 3. Ensure network connectivity
The ComfyUI machine needs network access to the Flame workstation on the Wiretap port (default: TCP 111 for portmap, plus dynamic Wiretap ports).

### 4. Restart ComfyUI

---

## Usage

### Basic Workflow: Load Clip → Process → Preview

1. Add a **🔥 Flame Wiretap Browser** node
2. Set the **hostname** to your Flame workstation's IP/hostname
3. Click **"Browse Flame"** to open the tree browser
4. Navigate: Projects → Library → Reel → select a Clip
5. Connect the Browser outputs to a **🔥 Flame Clip Loader** node
6. Set **start_frame** and **frame_count**
7. Connect the `images` output to your AI processing pipeline
8. Optionally connect to a **🔥 Flame Clip Writer** to write back

### Example Workflow

```
[Wiretap Browser] → [Clip Loader] → [Upscale Model] → [Clip Writer]
     hostname          start_frame      model_name       destination
     clip_node_id      frame_count                       clip_name
```

---

## Wiretap IFFFS Node Hierarchy

Understanding the Flame project structure as exposed by Wiretap:

```
/ (root)
└── /projects
    └── /<project_name>                    (PROJECT)
        ├── /workspace_id                  (WORKSPACE)
        └── /shared_libs_id               (LIBRARY_LIST)
            └── /<library_id>              (LIBRARY)
                └── /<reel_id>             (REEL)
                    └── /<clip_id>         (CLIP)
                        ├── /hires         (HIRES - full res frames)
                        └── /lowres        (LOWRES - proxy frames)
```

Each node has a unique ID (e.g., `/projects/MyProject/cc00000a_543d5e91_00053198/...`) that you can see in Flame's Wiretap tools via `wiretap_print_tree`.

---

## API Endpoints

The backend exposes REST endpoints for the frontend browser:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/wiretap/status` | GET | Check SDK availability and mock mode |
| `/wiretap/browse?hostname=&node_id=&server_type=` | GET | List children of a node |
| `/wiretap/clip_info?hostname=&node_id=` | GET | Get clip format details |

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WIRETAP_SDK_PATH` | Path to Wiretap Python SDK | Auto-detected |

### Auto-detected SDK Paths

The module searches these paths in order:
1. `$WIRETAP_SDK_PATH`
2. `/opt/Autodesk/wiretap/tools/current/python`
3. `/usr/discreet/wiretap/tools/current/python`

---

## Known Limitations

- **Write frame buffer encoding**: Direct frame writes via the IFFFS Wiretap API have known issues with buffer encoding (see [Logik forum discussion](https://forum.logik.tv/t/wiretap-api-create-clip-help-needed/8439)). The FrameWriter uses the Gateway server workaround.
- **Windows**: Wiretap SDK is no longer available on Windows (as of November 2023). Use Linux or macOS.
- **Authentication**: Wiretap does not use authentication; access is controlled by network visibility.
- **Large clips**: Loading many frames at high bit-depth requires significant memory. Use `max_dimension` to resize, or load frame ranges.

---

## Development

### Running in Mock Mode

Without the Wiretap SDK, all nodes operate in mock mode:
- The browser shows a simulated project hierarchy
- The loader generates gradient test patterns
- The writer logs what it would do

This lets you develop and test workflows without a Flame workstation.

### Project Structure

```
ComfyUI-WiretapBrowser/
├── __init__.py              # ComfyUI registration
├── wiretap_connection.py    # SDK wrapper, connection management, mock mode
├── wiretap_browser.py       # Browser node + API routes
├── wiretap_loader.py        # Clip Loader + Frame Writer nodes
├── frame_converter.py       # Raw RGB buffer → torch tensor conversion
├── js/
│   └── wiretap_browser.js   # Frontend tree browser UI
└── README.md
```

---

## Credits

- **Wiretap SDK** by Autodesk — [Developer docs](https://aps.autodesk.com/developer/overview/wiretap)
- Inspired by community work on [TimewarpML](https://forum.logik.tv/t/flame-machine-learning-timewarp-now-on-linux-and-mac/2038) by @talosh
- [CBS Digital Hiero-Wiretap](https://github.com/CBSDigital/Hiero-Wiretap) for Wiretap integration patterns
- [ShotGrid tk-flame](https://github.com/shotgunsoftware/tk-flame) for production Wiretap usage examples

## License

MIT
