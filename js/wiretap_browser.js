/**
 * ComfyUI Wiretap Browser - Frontend Extension
 *
 * Provides a visual tree browser widget for navigating Autodesk Flame's
 * Wiretap IFFFS hierarchy. Supports two modes:
 *
 *   SOURCE mode (WiretapBrowser node):
 *     Browse and select clips to load frames from.
 *     Server → Projects → Libraries → Reels → [select Clip]
 *
 *   DESTINATION mode (WiretapFrameWriter node):
 *     Browse and select a library or reel to write frames into.
 *     Server → Projects → Libraries → [select Reel or Library]
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// ─── Icon SVGs ───────────────────────────────────────────────────────────────

const ICONS = {
    server: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><circle cx="6" cy="6" r="1"/><circle cx="6" cy="18" r="1"/></svg>`,
    database: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
    "folder-open": `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,
    layout: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>`,
    library: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    "book-open": `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>`,
    film: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/><line x1="17" y1="17" x2="22" y2="17"/></svg>`,
    layers: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
    image: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`,
    maximize: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>`,
    minimize: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>`,
    upload: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></svg>`,
    download: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="8 17 12 21 16 17"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.88 18.09A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.29"/></svg>`,
    file: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
    loading: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>`,
    refresh: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`,
};

function getIcon(name) {
    return ICONS[name] || ICONS.file;
}

// ─── Mode Configuration ──────────────────────────────────────────────────────

const BROWSER_MODES = {
    source: {
        title: "Select Source Clip",
        icon: "download",
        accentColor: "#ff6b35",
        accentHover: "#e55a2b",
        accentBg: "#3d1e00",
        selectBtnLabel: "Select Clip",
        emptyHint: "Navigate to a clip to load",
        isSelectable: (child) => child.is_clip,
        showClipInfo: true,
    },
    destination: {
        title: "Select Write Destination",
        icon: "upload",
        accentColor: "#2ecc71",
        accentHover: "#27ae60",
        accentBg: "#0d3d1f",
        selectBtnLabel: "Select Destination",
        emptyHint: "Navigate to a library or reel to write into",
        isSelectable: (child) => {
            const t = child.node_type;
            return t === "LIBRARY" || t === "REEL" || t === "REEL_GROUP";
        },
        showClipInfo: false,
    },
};

// ─── Styles ──────────────────────────────────────────────────────────────────

const MODAL_STYLES = `
.wiretap-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.6); z-index: 10000;
    display: flex; align-items: center; justify-content: center;
}
.wiretap-modal {
    background: #1a1a2e; border: 1px solid #444; border-radius: 8px;
    width: 620px; max-height: 80vh; display: flex; flex-direction: column;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5); color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px;
}
.wiretap-header {
    padding: 12px 16px; border-bottom: 1px solid #333;
    display: flex; align-items: center; justify-content: space-between;
}
.wiretap-header h3 {
    margin: 0; font-size: 15px;
    display: flex; align-items: center; gap: 8px;
}
.wiretap-header .close-btn {
    background: none; border: none; color: #888; cursor: pointer;
    font-size: 20px; padding: 4px 8px; border-radius: 4px;
}
.wiretap-header .close-btn:hover { background: #333; color: #fff; }
.wiretap-header .refresh-btn {
    background: none; border: 1px solid #444; color: #888; cursor: pointer;
    font-size: 12px; padding: 4px 8px; border-radius: 4px;
    display: flex; align-items: center; gap: 4px;
}
.wiretap-header .refresh-btn:hover { background: #333; color: #fff; border-color: #666; }
.wiretap-header .refresh-btn.spinning svg {
    animation: wt-spin 0.8s linear infinite;
}
@keyframes wt-spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}
.wiretap-mode-badge {
    font-size: 10px; font-weight: 600; padding: 2px 8px;
    border-radius: 3px; text-transform: uppercase; letter-spacing: 0.5px;
}
.wiretap-breadcrumb {
    padding: 8px 16px; background: #16213e; border-bottom: 1px solid #333;
    display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
    font-size: 12px;
}
.wiretap-breadcrumb span.sep { color: #666; }
.wiretap-breadcrumb a {
    color: #5dade2; text-decoration: none; cursor: pointer;
}
.wiretap-breadcrumb a:hover { text-decoration: underline; }
.wiretap-tree {
    flex: 1; overflow-y: auto; padding: 8px 0; min-height: 200px;
    max-height: 400px;
}
.wiretap-node {
    padding: 6px 16px; cursor: pointer; display: flex; align-items: center;
    gap: 8px; transition: background 0.1s;
}
.wiretap-node:hover { background: #16213e; }
.wiretap-node.selected { background: #1a3a5c; }
.wiretap-node .icon { flex-shrink: 0; color: #5dade2; display: flex; }
.wiretap-node .name { flex: 1; }
.wiretap-node .type-badge {
    font-size: 10px; padding: 1px 6px; border-radius: 3px;
    background: #333; color: #888;
}
.wiretap-node .select-hint {
    font-size: 10px; padding: 1px 6px; border-radius: 3px;
    opacity: 0; transition: opacity 0.15s;
}
.wiretap-node:hover .select-hint { opacity: 1; }
.wiretap-info-panel {
    padding: 8px 16px; border-top: 1px solid #333;
    font-size: 12px; color: #aaa;
}
.wiretap-info-panel.clip-info {
    display: flex; flex-wrap: wrap; gap: 16px;
}
.wiretap-info-panel .info-item { display: flex; gap: 4px; }
.wiretap-info-panel .info-label { color: #5dade2; }
.wiretap-info-panel .dest-path {
    font-family: monospace; font-size: 11px; color: #2ecc71;
    word-break: break-all; margin-top: 4px;
}
.wiretap-footer {
    padding: 12px 16px; border-top: 1px solid #333;
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
}
.wiretap-footer .footer-left { font-size: 11px; color: #666; }
.wiretap-footer .footer-right { display: flex; gap: 8px; }
.wiretap-footer button {
    padding: 6px 16px; border-radius: 4px; border: 1px solid #444;
    cursor: pointer; font-size: 13px;
}
.wiretap-footer .btn-cancel { background: #333; color: #ccc; }
.wiretap-footer .btn-cancel:hover { background: #444; }
.wiretap-footer .btn-select:disabled {
    background: #555 !important; border-color: #555 !important;
    color: #888 !important; cursor: default !important;
}
.wiretap-empty { padding: 32px; text-align: center; color: #666; }
.wiretap-loading { padding: 32px; text-align: center; color: #5dade2; }
.wiretap-error {
    padding: 16px; margin: 8px 16px; background: #3d1414;
    border: 1px solid #622; border-radius: 4px; color: #e88;
}
.wiretap-mock-banner {
    padding: 6px 16px; background: #3d3d00; color: #dda;
    font-size: 11px; text-align: center; border-bottom: 1px solid #553;
}
`;

// ─── Browser Dialog ──────────────────────────────────────────────────────────

class WiretapBrowserDialog {
    /**
     * @param {string}   hostname    Flame workstation hostname
     * @param {string}   serverType  "IFFFS" or "Gateway"
     * @param {string}   mode        "source" or "destination"
     * @param {function} onSelect    callback(selectedNode)
     */
    constructor(hostname, serverType, mode, onSelect) {
        this.hostname = hostname;
        this.serverType = serverType;
        this.mode = mode;
        this.config = BROWSER_MODES[mode] || BROWSER_MODES.source;
        this.onSelect = onSelect;
        this.currentPath = "/";
        this.pathHistory = [{ path: "/", name: "Root" }];
        this.selectedNode = null;
        this.isMockMode = false;
        this.overlay = null;
        this._keyHandler = null;
    }

    async open() {
        if (!document.getElementById("wiretap-styles")) {
            const style = document.createElement("style");
            style.id = "wiretap-styles";
            style.textContent = MODAL_STYLES;
            document.head.appendChild(style);
        }

        try {
            const res = await api.fetchApi("/wiretap/status");
            const status = await res.json();
            this.isMockMode = status.mock_mode;
        } catch (e) {
            this.isMockMode = true;
        }

        this._buildModal();
        document.body.appendChild(this.overlay);
        this._loadChildren("/");
    }

    close() {
        if (this._keyHandler) {
            document.removeEventListener("keydown", this._keyHandler);
            this._keyHandler = null;
        }
        if (this.overlay) {
            this.overlay.remove();
            this.overlay = null;
        }
    }

    _buildModal() {
        const cfg = this.config;
        const modeLabel = this.mode === "destination" ? "WRITE TO" : "READ FROM";
        const modeIcon = getIcon(cfg.icon);

        this.overlay = document.createElement("div");
        this.overlay.className = "wiretap-overlay";
        this.overlay.addEventListener("click", (e) => {
            if (e.target === this.overlay) this.close();
        });

        const modal = document.createElement("div");
        modal.className = "wiretap-modal";
        modal.innerHTML = `
            <div class="wiretap-header">
                <h3 style="color: ${cfg.accentColor}">
                    ${modeIcon}
                    ${cfg.title} — ${this.hostname}
                    <span class="wiretap-mode-badge"
                        style="background: ${cfg.accentBg}; color: ${cfg.accentColor};">
                        ${modeLabel}
                    </span>
                </h3>
                <div style="display:flex; align-items:center; gap:8px;">
                    <button class="refresh-btn" id="wt-refresh" title="Refresh listing">${getIcon("refresh")} Refresh</button>
                    <button class="close-btn">&times;</button>
                </div>
            </div>
            ${this.isMockMode
                ? '<div class="wiretap-mock-banner">⚠ Mock Mode — Wiretap SDK not detected. Showing sample data.</div>'
                : ''}
            <div class="wiretap-breadcrumb" id="wt-breadcrumb"></div>
            <div class="wiretap-tree" id="wt-tree"></div>
            <div class="wiretap-info-panel" id="wt-info" style="display:none"></div>
            <div class="wiretap-footer">
                <div class="footer-left" id="wt-footer-hint">${cfg.emptyHint}</div>
                <div class="footer-right">
                    <button class="btn-cancel" id="wt-cancel">Cancel</button>
                    <button class="btn-select" id="wt-select" disabled
                        style="background: ${cfg.accentColor}; color: #fff; border-color: ${cfg.accentColor};">
                        ${cfg.selectBtnLabel}
                    </button>
                </div>
            </div>
        `;
        this.overlay.appendChild(modal);

        modal.querySelector(".close-btn").onclick = () => this.close();
        modal.querySelector("#wt-cancel").onclick = () => this.close();
        modal.querySelector("#wt-refresh").onclick = () => this._refresh();
        modal.querySelector("#wt-select").onclick = () => {
            if (this.selectedNode) {
                this.onSelect(this.selectedNode);
                this.close();
            }
        };

        this._keyHandler = (e) => { if (e.key === "Escape") this.close(); };
        document.addEventListener("keydown", this._keyHandler);
    }

    _updateBreadcrumb() {
        const bc = this.overlay.querySelector("#wt-breadcrumb");
        bc.innerHTML = "";
        this.pathHistory.forEach((item, idx) => {
            if (idx > 0) {
                const sep = document.createElement("span");
                sep.className = "sep";
                sep.textContent = " › ";
                bc.appendChild(sep);
            }
            const link = document.createElement("a");
            link.textContent = item.name;
            link.onclick = () => {
                this.pathHistory = this.pathHistory.slice(0, idx + 1);
                this.currentPath = item.path;
                this._clearSelection();
                this._loadChildren(item.path);
            };
            bc.appendChild(link);
        });
    }

    _clearSelection() {
        this.selectedNode = null;
        const btn = this.overlay.querySelector("#wt-select");
        if (btn) btn.disabled = true;
        const info = this.overlay.querySelector("#wt-info");
        if (info) info.style.display = "none";
        const hint = this.overlay.querySelector("#wt-footer-hint");
        if (hint) hint.textContent = this.config.emptyHint;
    }

    async _refresh() {
        const btn = this.overlay.querySelector("#wt-refresh");
        btn.classList.add("spinning");
        this._clearSelection();
        await this._loadChildren(this.currentPath, true);
        btn.classList.remove("spinning");
    }

    async _loadChildren(nodeId, refresh = false) {
        const tree = this.overlay.querySelector("#wt-tree");
        tree.innerHTML = `<div class="wiretap-loading">${getIcon("loading")} Loading...</div>`;
        this._updateBreadcrumb();

        try {
            const params = new URLSearchParams({
                hostname: this.hostname,
                node_id: nodeId,
                server_type: this.serverType,
            });
            if (refresh) params.set("refresh", "1");
            const res = await api.fetchApi(`/wiretap/browse?${params}`);
            const data = await res.json();

            if (!data.success) {
                tree.innerHTML = `<div class="wiretap-error">Error: ${data.error}</div>`;
                return;
            }
            if (data.children.length === 0) {
                tree.innerHTML = `<div class="wiretap-empty">No items found</div>`;
                return;
            }

            tree.innerHTML = "";
            const cfg = this.config;

            data.children.forEach((child) => {
                const row = document.createElement("div");
                row.className = "wiretap-node";

                const selectable = cfg.isSelectable(child);
                const canBrowse = child.has_children !== false;
                const iconColor = selectable ? cfg.accentColor : "#5dade2";

                const badgeStyle = selectable
                    ? `background:${cfg.accentBg}; color:${cfg.accentColor}`
                    : "";

                let hintHtml = "";
                if (selectable) {
                    const verb = this.mode === "destination" ? "write here" : "select";
                    hintHtml = `<span class="select-hint"
                        style="background:${cfg.accentBg}; color:${cfg.accentColor};">
                        click to ${verb}</span>`;
                }

                row.innerHTML = `
                    <span class="icon" style="color: ${iconColor}">${getIcon(child.icon)}</span>
                    <span class="name">${child.display_name}</span>
                    ${hintHtml}
                    <span class="type-badge" style="${badgeStyle}">${child.node_type}</span>
                `;

                // ── Click behavior ───────────────────────────────────────
                row.addEventListener("click", () => {
                    if (selectable) {
                        // Select this node
                        this.overlay.querySelectorAll(".wiretap-node.selected")
                            .forEach((n) => n.classList.remove("selected"));
                        row.classList.add("selected");
                        this.selectedNode = child;
                        this.overlay.querySelector("#wt-select").disabled = false;
                        this._showInfo(child);
                    } else if (canBrowse) {
                        this._browseInto(child);
                    }
                });

                // ── Double-click behavior ────────────────────────────────
                row.addEventListener("dblclick", () => {
                    if (selectable && this.mode === "destination" && canBrowse) {
                        // In destination mode, double-click on a selectable
                        // container browses into it (single click selects)
                        this._browseInto(child);
                    } else if (selectable) {
                        // In source mode, double-click confirms selection
                        this.selectedNode = child;
                        this.onSelect(child);
                        this.close();
                    }
                });

                tree.appendChild(row);
            });

        } catch (e) {
            tree.innerHTML = `<div class="wiretap-error">Connection error: ${e.message}</div>`;
        }
    }

    _browseInto(child) {
        this.currentPath = child.node_id;
        this.pathHistory.push({ path: child.node_id, name: child.display_name });
        this._clearSelection();
        this._loadChildren(child.node_id);
    }

    _showInfo(child) {
        const panel = this.overlay.querySelector("#wt-info");
        const hint = this.overlay.querySelector("#wt-footer-hint");

        if (this.mode === "source" && child.is_clip) {
            panel.className = "wiretap-info-panel clip-info";
            panel.style.display = "flex";
            panel.innerHTML = `
                <div class="info-item"><span class="info-label">Resolution:</span> ${child.width}×${child.height}</div>
                <div class="info-item"><span class="info-label">Frames:</span> ${child.num_frames}</div>
                <div class="info-item"><span class="info-label">Depth:</span> ${child.bit_depth}-bit</div>
                <div class="info-item"><span class="info-label">FPS:</span> ${child.fps}</div>
                <div class="info-item"><span class="info-label">Scan:</span> ${child.scan_format || 'N/A'}</div>
            `;
            hint.textContent = `Selected: ${child.display_name}`;

        } else if (this.mode === "destination") {
            panel.className = "wiretap-info-panel";
            panel.style.display = "block";
            panel.innerHTML = `
                <strong style="color: #2ecc71;">Write destination:</strong>
                ${child.display_name} (${child.node_type})
                <div class="dest-path">${child.node_id}</div>
            `;
            hint.textContent = `Clips will be created in: ${child.display_name}`;

        } else {
            panel.style.display = "none";
            hint.textContent = `Selected: ${child.display_name}`;
        }
    }
}


// ─── ComfyUI Extension ──────────────────────────────────────────────────────

app.registerExtension({
    name: "Wiretap.Browser",

    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {

        // ── Source Browser: WiretapBrowser ────────────────────────────────
        if (nodeData.name === "WiretapBrowser") {
            const orig = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                if (orig) orig.apply(this, arguments);

                this.addWidget("button", "🔥 Browse Source", null, () => {
                    const hostname = this.widgets.find(w => w.name === "hostname")?.value || "localhost";
                    const serverType = this.widgets.find(w => w.name === "server_type")?.value || "IFFFS";
                    const clipIdWidget = this.widgets.find(w => w.name === "clip_node_id");

                    const dialog = new WiretapBrowserDialog(
                        hostname, serverType, "source",
                        (clip) => {
                            if (clipIdWidget) clipIdWidget.value = clip.node_id;
                            app.graph.setDirtyCanvas(true);
                        }
                    );
                    dialog.open();
                }).serialize = false;
            };

            const origDraw = nodeType.prototype.onDrawForeground;
            nodeType.prototype.onDrawForeground = function (ctx) {
                if (origDraw) origDraw.apply(this, arguments);
                const w = this.widgets?.find(w => w.name === "clip_node_id");
                if (w?.value) {
                    ctx.fillStyle = "#ff6b35";
                    ctx.beginPath();
                    ctx.arc(this.size[0] - 14, 14, 5, 0, Math.PI * 2);
                    ctx.fill();
                }
            };
        }

        // ── WiretapFrameWriter writes EXR sequences to disk (no browser needed) ──
    },

    async setup() {
        api.addEventListener("wiretap.server.status", (event) => {
            console.log("[Wiretap]", event.detail.status);
        });
    },
});
