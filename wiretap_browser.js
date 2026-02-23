/**
 * ComfyUI Wiretap Browser - Frontend Extension
 *
 * Provides a visual tree browser widget for navigating Autodesk Flame's
 * Wiretap IFFFS hierarchy. When the user clicks "Browse" on the
 * WiretapBrowser node, a modal dialog opens showing the project tree:
 *
 *   Server → Projects → Libraries → Reels → Clips
 *
 * Selecting a clip populates the node's clip_node_id widget.
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Icon SVGs for different node types in the tree
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
    file: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
    loading: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>`,
};

function getIcon(iconName) {
    return ICONS[iconName] || ICONS.file;
}

// Styles for the browser modal
const MODAL_STYLES = `
.wiretap-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.6); z-index: 10000;
    display: flex; align-items: center; justify-content: center;
}
.wiretap-modal {
    background: #1a1a2e; border: 1px solid #444; border-radius: 8px;
    width: 600px; max-height: 80vh; display: flex; flex-direction: column;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5); color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px;
}
.wiretap-header {
    padding: 12px 16px; border-bottom: 1px solid #333;
    display: flex; align-items: center; justify-content: space-between;
}
.wiretap-header h3 {
    margin: 0; font-size: 15px; color: #ff6b35;
    display: flex; align-items: center; gap: 8px;
}
.wiretap-header .close-btn {
    background: none; border: none; color: #888; cursor: pointer;
    font-size: 20px; padding: 4px 8px; border-radius: 4px;
}
.wiretap-header .close-btn:hover { background: #333; color: #fff; }
.wiretap-breadcrumb {
    padding: 8px 16px; background: #16213e; border-bottom: 1px solid #333;
    display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
    font-size: 12px;
}
.wiretap-breadcrumb span { color: #666; }
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
.wiretap-node .icon.clip-icon { color: #ff6b35; }
.wiretap-node .name { flex: 1; }
.wiretap-node .type-badge {
    font-size: 10px; padding: 1px 6px; border-radius: 3px;
    background: #333; color: #888;
}
.wiretap-node .type-badge.clip { background: #3d1e00; color: #ff6b35; }
.wiretap-clip-info {
    padding: 8px 16px; background: #0f3460; border-top: 1px solid #333;
    font-size: 12px; color: #aaa;
    display: flex; flex-wrap: wrap; gap: 16px;
}
.wiretap-clip-info .info-item {
    display: flex; gap: 4px;
}
.wiretap-clip-info .info-label { color: #5dade2; }
.wiretap-footer {
    padding: 12px 16px; border-top: 1px solid #333;
    display: flex; justify-content: flex-end; gap: 8px;
}
.wiretap-footer button {
    padding: 6px 16px; border-radius: 4px; border: 1px solid #444;
    cursor: pointer; font-size: 13px;
}
.wiretap-footer .btn-cancel { background: #333; color: #ccc; }
.wiretap-footer .btn-cancel:hover { background: #444; }
.wiretap-footer .btn-select {
    background: #ff6b35; color: #fff; border-color: #ff6b35;
}
.wiretap-footer .btn-select:hover { background: #e55a2b; }
.wiretap-footer .btn-select:disabled {
    background: #555; border-color: #555; color: #888; cursor: default;
}
.wiretap-empty {
    padding: 32px; text-align: center; color: #666;
}
.wiretap-loading {
    padding: 32px; text-align: center; color: #5dade2;
}
.wiretap-error {
    padding: 16px; margin: 8px 16px; background: #3d1414;
    border: 1px solid #622; border-radius: 4px; color: #e88;
}
.wiretap-mock-banner {
    padding: 6px 16px; background: #3d3d00; color: #dda;
    font-size: 11px; text-align: center; border-bottom: 1px solid #553;
}
`;

class WiretapBrowserDialog {
    constructor(hostname, serverType, onSelect) {
        this.hostname = hostname;
        this.serverType = serverType;
        this.onSelect = onSelect;
        this.currentPath = "/";
        this.pathHistory = [{ path: "/", name: "Root" }];
        this.selectedNode = null;
        this.isMockMode = false;
        this.overlay = null;
    }

    async open() {
        // Inject styles
        if (!document.getElementById("wiretap-styles")) {
            const style = document.createElement("style");
            style.id = "wiretap-styles";
            style.textContent = MODAL_STYLES;
            document.head.appendChild(style);
        }

        // Check SDK status
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
        if (this.overlay) {
            this.overlay.remove();
            this.overlay = null;
        }
    }

    _buildModal() {
        this.overlay = document.createElement("div");
        this.overlay.className = "wiretap-overlay";
        this.overlay.addEventListener("click", (e) => {
            if (e.target === this.overlay) this.close();
        });

        const modal = document.createElement("div");
        modal.className = "wiretap-modal";

        // Header
        modal.innerHTML = `
            <div class="wiretap-header">
                <h3>
                    ${getIcon("film")}
                    Wiretap Browser — ${this.hostname}
                </h3>
                <button class="close-btn">&times;</button>
            </div>
            ${this.isMockMode ? '<div class="wiretap-mock-banner">⚠ Mock Mode — Wiretap SDK not detected. Showing sample data.</div>' : ''}
            <div class="wiretap-breadcrumb" id="wt-breadcrumb"></div>
            <div class="wiretap-tree" id="wt-tree"></div>
            <div class="wiretap-clip-info" id="wt-clip-info" style="display:none"></div>
            <div class="wiretap-footer">
                <button class="btn-cancel" id="wt-cancel">Cancel</button>
                <button class="btn-select" id="wt-select" disabled>Select Clip</button>
            </div>
        `;

        this.overlay.appendChild(modal);

        modal.querySelector(".close-btn").onclick = () => this.close();
        modal.querySelector("#wt-cancel").onclick = () => this.close();
        modal.querySelector("#wt-select").onclick = () => {
            if (this.selectedNode) {
                this.onSelect(this.selectedNode);
                this.close();
            }
        };
    }

    _updateBreadcrumb() {
        const container = this.overlay.querySelector("#wt-breadcrumb");
        container.innerHTML = "";

        this.pathHistory.forEach((item, idx) => {
            if (idx > 0) {
                const sep = document.createElement("span");
                sep.textContent = " › ";
                container.appendChild(sep);
            }
            const link = document.createElement("a");
            link.textContent = item.name;
            link.onclick = () => {
                this.pathHistory = this.pathHistory.slice(0, idx + 1);
                this.currentPath = item.path;
                this._loadChildren(item.path);
            };
            container.appendChild(link);
        });
    }

    async _loadChildren(nodeId) {
        const tree = this.overlay.querySelector("#wt-tree");
        tree.innerHTML = `<div class="wiretap-loading">${getIcon("loading")} Loading...</div>`;
        this._updateBreadcrumb();

        try {
            const params = new URLSearchParams({
                hostname: this.hostname,
                node_id: nodeId,
                server_type: this.serverType,
            });
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
            data.children.forEach((child) => {
                const row = document.createElement("div");
                row.className = "wiretap-node";

                const isClip = child.is_clip;
                const iconClass = isClip ? "icon clip-icon" : "icon";
                const typeBadgeClass = isClip ? "type-badge clip" : "type-badge";

                row.innerHTML = `
                    <span class="${iconClass}">${getIcon(child.icon)}</span>
                    <span class="name">${child.display_name}</span>
                    <span class="${typeBadgeClass}">${child.node_type}</span>
                `;

                row.addEventListener("click", () => {
                    if (isClip) {
                        // Select this clip
                        this.overlay.querySelectorAll(".wiretap-node.selected")
                            .forEach((n) => n.classList.remove("selected"));
                        row.classList.add("selected");
                        this.selectedNode = child;
                        this.overlay.querySelector("#wt-select").disabled = false;
                        this._showClipInfo(child);
                    } else if (child.has_children !== false) {
                        // Browse into this node
                        this.currentPath = child.node_id;
                        this.pathHistory.push({
                            path: child.node_id,
                            name: child.display_name,
                        });
                        this.selectedNode = null;
                        this.overlay.querySelector("#wt-select").disabled = true;
                        this.overlay.querySelector("#wt-clip-info").style.display = "none";
                        this._loadChildren(child.node_id);
                    }
                });

                // Double-click on clips also selects and confirms
                if (isClip) {
                    row.addEventListener("dblclick", () => {
                        this.selectedNode = child;
                        this.onSelect(child);
                        this.close();
                    });
                }

                tree.appendChild(row);
            });

        } catch (e) {
            tree.innerHTML = `<div class="wiretap-error">Connection error: ${e.message}</div>`;
        }
    }

    _showClipInfo(clip) {
        const info = this.overlay.querySelector("#wt-clip-info");
        if (!clip.is_clip) {
            info.style.display = "none";
            return;
        }
        info.style.display = "flex";
        info.innerHTML = `
            <div class="info-item"><span class="info-label">Resolution:</span> ${clip.width}×${clip.height}</div>
            <div class="info-item"><span class="info-label">Frames:</span> ${clip.num_frames}</div>
            <div class="info-item"><span class="info-label">Depth:</span> ${clip.bit_depth}-bit</div>
            <div class="info-item"><span class="info-label">FPS:</span> ${clip.fps}</div>
            <div class="info-item"><span class="info-label">Scan:</span> ${clip.scan_format || 'N/A'}</div>
        `;
    }
}


// Register the ComfyUI extension
app.registerExtension({
    name: "Wiretap.Browser",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "WiretapBrowser") return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (origOnNodeCreated) origOnNodeCreated.apply(this, arguments);

            // Add "Browse" button widget
            const browseBtn = this.addWidget("button", "Browse Flame", null, () => {
                const hostnameWidget = this.widgets.find(w => w.name === "hostname");
                const serverTypeWidget = this.widgets.find(w => w.name === "server_type");
                const clipIdWidget = this.widgets.find(w => w.name === "clip_node_id");

                const hostname = hostnameWidget?.value || "localhost";
                const serverType = serverTypeWidget?.value || "IFFFS";

                const dialog = new WiretapBrowserDialog(hostname, serverType, (clip) => {
                    if (clipIdWidget) {
                        clipIdWidget.value = clip.node_id;
                    }
                    // Trigger node update
                    app.graph.setDirtyCanvas(true);
                });

                dialog.open();
            });

            // Style the browse button
            browseBtn.serialize = false;
        };

        // Customize the node appearance
        const origOnDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            if (origOnDrawForeground) origOnDrawForeground.apply(this, arguments);

            // Show a small Flame icon indicator
            const clipIdWidget = this.widgets?.find(w => w.name === "clip_node_id");
            if (clipIdWidget?.value) {
                ctx.fillStyle = "#ff6b35";
                ctx.beginPath();
                ctx.arc(this.size[0] - 14, 14, 5, 0, Math.PI * 2);
                ctx.fill();
            }
        };
    },

    async setup() {
        // Listen for server status messages
        api.addEventListener("wiretap.server.status", (event) => {
            console.log("[Wiretap]", event.detail.status);
        });
    },
});
