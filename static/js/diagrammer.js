/*
FILE:         diagrammer.js
DESCRIPTION:  Client-side rendering + interaction logic for the BEJSON
              Diagrammer, Flask/MFDB edition. Every meaningful mutation is
              persisted immediately through the /api/* endpoints backed by
              src/diagram_mfdb.py — each shape on the board is its own MFDB
              entity file server-side; this file only ever sees the merged
              view (Board fields + the shape's own fields) and translates
              between that server shape and the short local property names
              used by the renderer (cx, cy, sizeClass, kind, label, ...).
VERSION:      1.1.0
DATE:         2026-08-12
AUTHOR:       Elton Boehnen
CONTACT:      eltonboehnen@gmail.com | boehnenelton2024.pages.dev | github.com/boehnenelton
RELATIONAL_ID: 2f6a9c1e-8b4d-4a03-9e7f-5c1a3d8b6f92
CHANGELOG:
  v1.1.0 - Added importMfdbFile(), the client half of the new single-file
           MFDB-132 import endpoint (POST /api/import). Export button label
           updated to reflect the export format change (.zip -> .mfdb132.bejson).
*/

const VER = "1.0.0";

const app = (() => {
    const SIZE_CLASSES = {
        small:  { w:100, h:100 },
        medium: { w:200, h:100 },
        large:  { w:300, h:150 },
        huge:   { w:600, h:300 }
    };
    const TERR_PALETTE = ['#2a6496','#2e7d32','#e65100','#6a1a8a','#b71c1c','#00838f'];
    const TERR_STEP = 40, TERR_MIN = 80;

    // Known base server field names — anything else on a shape record is a
    // caller-defined custom field living on that shape's own MFDB entity.
    const KNOWN_SHAPE_FIELDS = new Set([
        "shape_id","shape_entity_name","shape_cx","shape_cy","shape_size_class",
        "shape_kind","shape_parent_fk","shape_collapsed","board_membership_fk",
        "shape_label","shape_color","shape_font_color","shape_text"
    ]);

    const S = {
        name: "Untitled Diagram",
        shapes: [], connectors: [], territories: [],
        selIds: [], selConn: null, selTerritory: null,
        zoom: 0.75,
        multi: false, snap: true, snapSz: 50,
        pending: null,
        drag: null, dragT: null, pan: null,
        objOpen: false
    };

    const D = {};
    function initDOM() {
        D.svg   = document.getElementById('canvas');
        D.cnt   = document.getElementById('canvas-container');
        D.vp    = document.getElementById('viewport');
        D.rt    = document.getElementById('ruler-top');
        D.rl    = document.getElementById('ruler-left');
        D.stl   = document.getElementById('st-l');
        D.str   = document.getElementById('st-r');
        D.drw   = document.getElementById('drawer');
        D.fab   = document.getElementById('fabWrap');
        D.lr    = document.getElementById('lightroom');
        D.lri   = document.getElementById('lr-input');
        D.lrv   = document.getElementById('lr-view');
        D.lrt   = document.getElementById('lr-title');
        D.iname = document.getElementById('inp-name');
        D.op    = document.getElementById('obj-panel');
        D.ofab  = document.getElementById('obj-fab');
        D.ol    = document.getElementById('obj-list');
        D.oc    = document.getElementById('obj-count');
    }

    // ── SERVER <-> LOCAL TRANSLATION ─────────────────────
    function shapeFromServer(s) {
        const custom = {};
        Object.keys(s).forEach(k => { if (!KNOWN_SHAPE_FIELDS.has(k)) custom[k] = s[k]; });
        return {
            id: s.shape_id, cx: s.shape_cx, cy: s.shape_cy,
            sizeClass: s.shape_size_class, kind: s.shape_kind,
            parentId: s.shape_parent_fk, collapsed: !!s.shape_collapsed,
            label: s.shape_label, color: s.shape_color,
            fontColor: s.shape_font_color, text: s.shape_text,
            custom
        };
    }
    function connFromServer(c) {
        return { id: c.conn_id, from: c.conn_from_fk, to: c.conn_to_fk, flow: c.conn_flow };
    }
    function terrFromServer(t) {
        return { id: t.terr_id, kind: t.terr_kind, cx: t.terr_cx, cy: t.terr_cy, size: t.terr_size, label: t.terr_label, color: t.terr_color };
    }

    // ── API LAYER ────────────────────────────────────────
    async function apiGet(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`GET ${url} -> ${r.status}`);
        return r.json();
    }
    async function apiSend(url, method, body) {
        const r = await fetch(url, {
            method, headers: { 'Content-Type': 'application/json' },
            body: body !== undefined ? JSON.stringify(body) : undefined
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({ error: r.statusText }));
            throw new Error(err.error || `${method} ${url} -> ${r.status}`);
        }
        return r.status === 204 ? null : r.json();
    }

    async function loadDiagram() {
        const data = await apiGet('/api/diagram');
        S.name = data.name;
        S.diagramId = data.diagramId;
        S.shapes = data.shapes.map(shapeFromServer);
        S.connectors = data.connectors.map(connFromServer);
        S.territories = data.territories.map(terrFromServer);
        D.iname.value = S.name;
    }

    function applyDiagramPayload(data) {
        S.name = data.name;
        S.diagramId = data.diagramId;
        S.shapes = data.shapes.map(shapeFromServer);
        S.connectors = data.connectors.map(connFromServer);
        S.territories = data.territories.map(terrFromServer);
        S.selIds = []; S.selConn = null; S.selTerritory = null; S.pending = null;
        D.iname.value = S.name;
    }

    async function loadRecentDiagramsList() {
        try {
            const data = await apiGet('/api/diagrams');
            const sel = document.getElementById('diagram-select');
            sel.innerHTML = '<option value="">Load a diagram…</option>' +
                data.recent.map(r => `<option value="${r.diagram_id}"${r.diagram_id === data.current ? ' selected' : ''}>${esc(r.name)}</option>`).join('');
        } catch (e) {
            D.stl.innerText = 'Diagram list failed: ' + e.message;
        }
    }

    async function newDiagramPrompt() {
        const name = window.prompt('Name for the new diagram:', 'Untitled Diagram');
        if (!name) return;
        D.stl.innerText = 'Creating diagram…';
        try {
            const res = await apiSend('/api/diagrams/new', 'POST', { name });
            applyDiagramPayload(res.diagram);
            render(); renderObj(); updateUI();
            await loadRecentDiagramsList();
            D.stl.innerText = 'Ready';
        } catch (e) {
            D.stl.innerText = 'Create failed: ' + e.message;
        }
    }

    async function loadDiagramFromSelect(diagramId) {
        if (!diagramId) return;
        D.stl.innerText = 'Loading diagram…';
        try {
            const data = await apiSend('/api/diagrams/load', 'POST', { diagramId });
            applyDiagramPayload(data);
            render(); renderObj(); updateUI();
            D.stl.innerText = 'Ready';
        } catch (e) {
            D.stl.innerText = 'Load failed: ' + e.message;
        }
    }

    async function saveDiagram() {
        // All mutations already persist immediately; "Save" here confirms
        // the current name/state and bumps this diagram's recency in the
        // Load combo box's registry.
        D.stl.innerText = 'Saving…';
        try {
            await apiSend('/api/diagram/name', 'PUT', { name: S.name });
            await loadRecentDiagramsList();
            D.stl.innerText = 'Saved';
            setTimeout(() => { D.stl.innerText = 'Ready'; }, 1200);
        } catch (e) {
            D.stl.innerText = 'Save failed: ' + e.message;
        }
    }

    // ── GEOMETRY HELPERS ─────────────────────────────────
    function dims(s) { return SIZE_CLASSES[s.sizeClass] || SIZE_CLASSES.medium; }
    function boxOf(s) {
        const d = dims(s);
        return { x: s.cx - d.w/2, y: s.cy - d.h/2, w: d.w, h: d.h };
    }
    function territoryBox(t) {
        return { x: t.cx - t.size/2, y: t.cy - t.size/2, w: t.size, h: t.size };
    }
    function rectsOverlap(a, b) {
        return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
    }
    function shapeOverlapsAny(testShape, excludeIds) {
        const boxA = boxOf(testShape);
        return S.shapes.some(s => !excludeIds.includes(s.id) && rectsOverlap(boxA, boxOf(s)));
    }
    function territoryOverlapsAny(testTerr, excludeId) {
        const boxA = territoryBox(testTerr);
        return S.territories.some(t => t.id !== excludeId && rectsOverlap(boxA, territoryBox(t)));
    }
    function findContainingTerritory(s) {
        for (const t of S.territories) {
            if (t.kind === 'circle') {
                const dx = s.cx - t.cx, dy = s.cy - t.cy;
                if (Math.sqrt(dx*dx + dy*dy) <= t.size/2) return t;
            } else {
                const b = territoryBox(t);
                if (s.cx >= b.x && s.cx <= b.x+b.w && s.cy >= b.y && s.cy <= b.y+b.h) return t;
            }
        }
        return null;
    }

    // ── HIERARCHY UTILS ──────────────────────────────────
    function genOf(id) {
        let g = 0, cur = S.shapes.find(s => s.id === id);
        while (cur && cur.parentId) {
            cur = S.shapes.find(s => s.id === cur.parentId);
            if (++g > 200) break;
        }
        return g;
    }
    function childrenOf(pid) { return S.shapes.filter(s => s.parentId === pid); }
    function descendants(pid, out = new Set()) {
        childrenOf(pid).forEach(c => { out.add(c.id); descendants(c.id, out); });
        return out;
    }
    function hiddenSet() {
        const h = new Set();
        S.shapes.forEach(s => { if (s.collapsed) descendants(s.id).forEach(id => h.add(id)); });
        return h;
    }

    function snp(v) { return S.snap ? Math.round(v / S.snapSz) * S.snapSz : v; }

    // ── INIT ─────────────────────────────────────────────
    async function init() {
        initDOM();
        drawRulers();
        D.cnt.style.transform = `scale(${S.zoom})`;
        D.str.innerText = Math.round(S.zoom * 100) + '%';

        D.stl.innerText = 'Loading diagram…';
        try {
            await loadDiagram();
            await loadRecentDiagramsList();
        } catch (e) {
            D.stl.innerText = 'Load failed: ' + e.message;
        }

        window.addEventListener('pointermove', onMove, { passive: false });
        window.addEventListener('pointerup',   onUp);
        window.addEventListener('pointercancel', onUp);
        D.svg.addEventListener('pointerdown', onBg);
        D.vp.addEventListener('scroll', updateRulers);

        setupObjDrag();
        render();
        renderObj();
        D.stl.innerText = 'Ready';
    }

    // ── ADD SHAPES ───────────────────────────────────────
    async function addShape(cx, cy) {
        if (cx === undefined) {
            const r = D.vp.getBoundingClientRect();
            cx = (D.vp.scrollLeft + r.width/2) / S.zoom;
            cy = (D.vp.scrollTop  + r.height/2) / S.zoom;
        }
        const probe = { cx: snp(cx), cy: snp(cy), sizeClass: 'medium' };
        for (let i = 0; i < 40; i++) {
            if (!shapeOverlapsAny(probe, [])) break;
            probe.cx = snp(probe.cx + dims(probe).w + 30);
        }
        D.stl.innerText = 'Creating shape…';
        try {
            const created = await apiSend('/api/shape', 'POST', {
                cx: probe.cx, cy: probe.cy, sizeClass: 'medium', kind: 'rect',
                color: '#334', fontColor: 'auto', label: 'Node', text: '',
                parentFk: null, collapsed: false
            });
            const s = shapeFromServer(created);
            S.shapes.push(s);
            sel([s.id]); render(); renderObj();
            D.stl.innerText = 'Ready';
        } catch (e) {
            D.stl.innerText = 'Create failed: ' + e.message;
        }
    }

    async function addChildNode() {
        if (S.selIds.length !== 1) return;
        const pid = S.selIds[0];
        const par = S.shapes.find(s => s.id === pid);
        if (!par) return;
        const sizeClass = 'small';
        const pos = autoPlace(par, sizeClass);
        const gen = genOf(pid) + 1;
        const CCOLS = ['#2a3a5c','#1a4a2a','#4a3a1a','#3a1a4a','#334'];
        const color = CCOLS[Math.min(gen-1, CCOLS.length-1)];

        D.stl.innerText = 'Creating child…';
        try {
            const created = await apiSend('/api/shape', 'POST', {
                cx: pos.cx, cy: pos.cy, sizeClass, kind: 'rect', color, fontColor: 'auto',
                label: 'Node', text: '', parentFk: pid, collapsed: false
            });
            const s = shapeFromServer(created);
            S.shapes.push(s);
            const conn = await apiSend('/api/connector', 'POST', { fromFk: pid, toFk: s.id, flow: 'forward' });
            S.connectors.push(connFromServer(conn));
            sel([s.id]); render(); renderObj();
            D.stl.innerText = 'Ready';
        } catch (e) {
            D.stl.innerText = 'Create failed: ' + e.message;
        }
    }

    function autoPlace(par, sizeClass) {
        const GAP_Y=80, GAP_X=20;
        const d = SIZE_CLASSES[sizeClass];
        const sibs = childrenOf(par.id);
        let cx, cy;
        if (!sibs.length) {
            cx = par.cx;
            cy = boxOf(par).y + boxOf(par).h + GAP_Y + d.h/2;
        } else {
            let maxRight = -Infinity;
            sibs.forEach(sb => { const b = boxOf(sb); if (b.x+b.w > maxRight) maxRight = b.x+b.w; });
            cx = maxRight + GAP_X + d.w/2;
            cy = sibs[0].cy;
        }
        cx = snp(cx); cy = snp(cy);
        const probe = { cx, cy, sizeClass };
        for (let i = 0; i < 25; i++) {
            if (!shapeOverlapsAny(probe, [])) break;
            probe.cx = snp(probe.cx + d.w + GAP_X);
        }
        return { cx: probe.cx, cy: probe.cy };
    }

    async function makeRoot() {
        for (const id of S.selIds) {
            const s = S.shapes.find(x=>x.id===id);
            if (s) {
                s.parentId = null;
                try { await apiSend(`/api/shape/${id}`, 'PUT', { parentFk: null }); }
                catch (e) { D.stl.innerText = 'Update failed: ' + e.message; }
            }
        }
        render(); renderObj(); updateUI();
    }

    async function toggleCollapse(e, id) {
        e.stopPropagation();
        const s = S.shapes.find(x=>x.id===id);
        if (!s) return;
        s.collapsed = !s.collapsed;
        render(); renderObj();
        try { await apiSend(`/api/shape/${id}`, 'PUT', { collapsed: s.collapsed }); }
        catch (e2) { D.stl.innerText = 'Update failed: ' + e2.message; }
    }

    // ── SELECTION ────────────────────────────────────────
    function sel(ids) {
        S.selConn = null;
        S.selTerritory = null;
        if (S.multi) {
            ids.forEach(id => {
                if (S.selIds.includes(id)) S.selIds = S.selIds.filter(x=>x!==id);
                else S.selIds.push(id);
            });
        } else {
            S.selIds = ids;
        }
        updateUI(); render();
    }

    function selFromPanel(e, id) {
        e.stopPropagation();
        sel([id]);
        const s = S.shapes.find(x => x.id === id);
        if (s) {
            const b = boxOf(s);
            D.vp.scrollLeft = Math.max(0, b.x * S.zoom - 120);
            D.vp.scrollTop  = Math.max(0, b.y * S.zoom - 120);
        }
        renderObj();
    }

    // ── SHAPE DRAG ───────────────────────────────────────
    function onShapeDown(e, id) {
        e.stopPropagation();
        e.preventDefault();
        S.selTerritory = null;

        if (!S.multi && !S.selIds.includes(id)) {
            S.selIds = [id]; updateUI(); render();
        }

        const movIds = [...S.selIds];
        S.drag = {
            startX: e.clientX, startY: e.clientY, active: false,
            init: movIds.map(mid => {
                const s = S.shapes.find(x=>x.id===mid);
                return { id:mid, cx:s.cx, cy:s.cy };
            })
        };
    }

    async function validateDragResult() {
        const movedIds = S.drag.init.map(p => p.id);
        let collision = false;
        for (const p of S.drag.init) {
            const s = S.shapes.find(x => x.id === p.id);
            if (s && shapeOverlapsAny(s, movedIds)) { collision = true; break; }
        }
        if (collision) {
            S.drag.init.forEach(p => {
                const s = S.shapes.find(x => x.id === p.id);
                if (s) { s.cx = p.cx; s.cy = p.cy; }
            });
            D.stl.innerText = '⚠ Move blocked — overlap';
            setTimeout(() => { D.stl.innerText = 'Ready'; }, 2000);
            return;
        }
        for (const p of S.drag.init) {
            const s = S.shapes.find(x => x.id === p.id);
            if (!s) continue;
            try { await apiSend(`/api/shape/${s.id}`, 'PUT', { cx: s.cx, cy: s.cy }); }
            catch (e) { D.stl.innerText = 'Move save failed: ' + e.message; }
        }
    }

    function onMove(e) {
        if (S.pan) {
            D.vp.scrollLeft = S.pan.scrollX - (e.clientX - S.pan.startX);
            D.vp.scrollTop  = S.pan.scrollY - (e.clientY - S.pan.startY);
            return;
        }
        if (S.dragT) {
            const dx = (e.clientX - S.dragT.startX) / S.zoom;
            const dy = (e.clientY - S.dragT.startY) / S.zoom;
            if (!S.dragT.active && Math.hypot(dx,dy) > 8) S.dragT.active = true;
            if (S.dragT.active) {
                e.preventDefault();
                const t = S.territories.find(x => x.id === S.dragT.id);
                if (t) { t.cx = snp(S.dragT.initCx+dx); t.cy = snp(S.dragT.initCy+dy); render(); }
            }
            return;
        }
        if (!S.drag) return;
        const dx = (e.clientX - S.drag.startX) / S.zoom;
        const dy = (e.clientY - S.drag.startY) / S.zoom;
        if (!S.drag.active && Math.hypot(dx,dy) > 8) S.drag.active = true;
        if (S.drag.active) {
            e.preventDefault();
            S.drag.init.forEach(p => {
                const s = S.shapes.find(x=>x.id===p.id);
                if (s) { s.cx = snp(p.cx+dx); s.cy = snp(p.cy+dy); }
            });
            render();
        }
    }

    async function onUp() {
        if (S.drag && S.drag.active) await validateDragResult();
        if (S.dragT && S.dragT.active) {
            const t = S.territories.find(x => x.id === S.dragT.id);
            if (t) {
                if (territoryOverlapsAny(t, t.id)) {
                    t.cx = S.dragT.initCx; t.cy = S.dragT.initCy;
                    D.stl.innerText = '⚠ Territory blocked — overlap';
                    setTimeout(() => { D.stl.innerText = 'Ready'; }, 2000);
                } else {
                    try { await apiSend(`/api/territory/${t.id}`, 'PUT', { cx: t.cx, cy: t.cy }); }
                    catch (e) { D.stl.innerText = 'Territory save failed: ' + e.message; }
                }
            }
        }
        S.drag = null; S.dragT = null; S.pan = null;
        D.svg.classList.remove('panning');
        render(); renderObj();
    }

    function onBg(e) {
        if (e.target !== D.svg) return;
        if (S.selIds.length || S.selConn || S.pending || S.selTerritory) {
            S.selIds = []; S.selConn = null; S.pending = null; S.selTerritory = null;
            updateUI(); render(); renderTerrTab();
        }
        S.pan = { startX: e.clientX, startY: e.clientY,
                  scrollX: D.vp.scrollLeft, scrollY: D.vp.scrollTop };
        D.svg.classList.add('panning');
        e.preventDefault();
    }

    // ── CENTER CONNECTOR DOT ─────────────────────────────
    async function onCenterDown(e, shapeId) {
        e.stopPropagation(); e.preventDefault();
        if (S.pending) {
            if (S.pending.shapeId !== shapeId) {
                const p = S.pending;
                const dup = S.connectors.some(c =>
                    (c.from === p.shapeId && c.to === shapeId) ||
                    (c.from === shapeId   && c.to === p.shapeId)
                );
                if (!dup) {
                    try {
                        const conn = await apiSend('/api/connector', 'POST', { fromFk: p.shapeId, toFk: shapeId, flow: 'none' });
                        S.connectors.push(connFromServer(conn));
                    } catch (err) {
                        D.stl.innerText = 'Connector failed: ' + err.message;
                    }
                } else {
                    D.stl.innerText = '⚠ Duplicate blocked';
                    setTimeout(()=>{ D.stl.innerText='Ready'; }, 2000);
                }
            }
            S.pending = null;
        } else {
            S.pending = { shapeId };
            D.stl.innerText = 'Tap target shape center…';
        }
        render();
    }

    function onConnDown(e, id) {
        e.stopPropagation();
        S.selIds = []; S.selConn = id; S.selTerritory = null;
        updateUI(); render();
    }

    // ── CONNECTOR EDGE MATH ──────────────────────────────
    function shapeCenter(s) { return { x: s.cx, y: s.cy }; }

    function edgePoint(cx1, cy1, s) {
        const box = boxOf(s);
        const cx2 = s.cx, cy2 = s.cy;
        const dx = cx1 - cx2, dy = cy1 - cy2;
        if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return { x: cx2, y: cy2 };

        let tMin = Infinity;
        const tryT = (t) => {
            if (t <= 1e-6) return;
            const xi = cx2 + t*dx, yi = cy2 + t*dy;
            if (xi >= box.x-0.5 && xi <= box.x+box.w+0.5 &&
                yi >= box.y-0.5 && yi <= box.y+box.h+0.5) {
                if (t < tMin) tMin = t;
            }
        };
        if (Math.abs(dx) > 0.001) { tryT((box.x - cx2)/dx); tryT((box.x+box.w - cx2)/dx); }
        if (Math.abs(dy) > 0.001) { tryT((box.y - cy2)/dy); tryT((box.y+box.h - cy2)/dy); }

        if (tMin === Infinity) return { x: cx2, y: cy2 };
        return { x: cx2 + tMin*dx, y: cy2 + tMin*dy };
    }

    // ── TERRITORY ACTIONS ────────────────────────────────
    async function addTerritory(kind) {
        const r = D.vp.getBoundingClientRect();
        let cx = snp((D.vp.scrollLeft + r.width/2) / S.zoom);
        let cy = snp((D.vp.scrollTop  + r.height/2) / S.zoom);
        const size = 240;
        const probe = { cx, cy, size };
        for (let i = 0; i < 30; i++) {
            if (!territoryOverlapsAny(probe, null)) break;
            probe.cx = snp(probe.cx + size + 30);
        }
        const label = 'Territory ' + (S.territories.length + 1);
        const color = TERR_PALETTE[S.territories.length % TERR_PALETTE.length];
        try {
            const created = await apiSend('/api/territory', 'POST', { kind, cx: probe.cx, cy: probe.cy, size, label, color });
            const t = terrFromServer(created);
            S.territories.push(t);
            S.selIds = []; S.selConn = null; S.selTerritory = t.id;
            updateUI(); render(); renderObj(); renderTerrTab();
        } catch (e) {
            D.stl.innerText = 'Territory create failed: ' + e.message;
        }
    }

    function terrSelect(e, id) {
        e.stopPropagation(); e.preventDefault();
        S.selIds = []; S.selConn = null; S.selTerritory = id;
        const t = S.territories.find(x => x.id === id);
        S.dragT = { id, startX: e.clientX, startY: e.clientY, initCx: t.cx, initCy: t.cy, active: false };
        updateUI(); render(); renderTerrTab();
    }

    async function terrGrow(e) {
        e.stopPropagation(); e.preventDefault();
        if (!S.selTerritory) return;
        const t = S.territories.find(x => x.id === S.selTerritory);
        if (!t) return;
        const old = t.size;
        t.size += TERR_STEP;
        if (territoryOverlapsAny(t, t.id)) {
            t.size = old;
            D.stl.innerText = '⚠ Territory blocked — touching neighbor';
            setTimeout(() => { D.stl.innerText = 'Ready'; }, 2000);
        } else {
            try { await apiSend(`/api/territory/${t.id}`, 'PUT', { size: t.size }); }
            catch (e2) { D.stl.innerText = 'Resize failed: ' + e2.message; }
        }
        render(); renderObj(); renderTerrTab();
    }

    async function terrShrink(e) {
        e.stopPropagation(); e.preventDefault();
        if (!S.selTerritory) return;
        const t = S.territories.find(x => x.id === S.selTerritory);
        if (!t) return;
        t.size = Math.max(TERR_MIN, t.size - TERR_STEP);
        try { await apiSend(`/api/territory/${t.id}`, 'PUT', { size: t.size }); }
        catch (e2) { D.stl.innerText = 'Resize failed: ' + e2.message; }
        render(); renderObj(); renderTerrTab();
    }

    let colorEditTerrId = null;
    function openTerrColor(e, id) {
        e.stopPropagation(); e.preventDefault();
        colorEditTerrId = id;
        document.getElementById('terr-color-hidden').click();
    }
    async function setTerrColorFromPicker(val) {
        const t = S.territories.find(x => x.id === colorEditTerrId);
        if (!t) return;
        t.color = val; render(); renderObj();
        try { await apiSend(`/api/territory/${t.id}`, 'PUT', { color: val }); }
        catch (e) { D.stl.innerText = 'Color save failed: ' + e.message; }
    }

    let terrLabelDebounce = null;
    function setTerrLabel(val) {
        if (!S.selTerritory) return;
        const t = S.territories.find(x => x.id === S.selTerritory);
        if (!t) return;
        t.label = val; render(); renderObj();
        clearTimeout(terrLabelDebounce);
        terrLabelDebounce = setTimeout(async () => {
            try { await apiSend(`/api/territory/${t.id}`, 'PUT', { label: val }); }
            catch (e) { D.stl.innerText = 'Label save failed: ' + e.message; }
        }, 400);
    }

    function selTerrPanel(e, id) {
        e.stopPropagation();
        S.selIds = []; S.selConn = null; S.selTerritory = id;
        const t = S.territories.find(x => x.id === id);
        if (t) {
            D.vp.scrollLeft = Math.max(0, (t.cx - t.size/2) * S.zoom - 120);
            D.vp.scrollTop  = Math.max(0, (t.cy - t.size/2) * S.zoom - 120);
        }
        updateUI(); render(); renderObj(); renderTerrTab();
    }

    function switchTab(name) {
        document.getElementById('tab-props').style.display = name === 'props' ? 'block' : 'none';
        document.getElementById('tab-terr').style.display  = name === 'terr'  ? 'block' : 'none';
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
        if (name === 'terr') renderTerrTab();
    }

    function renderTerrTab() {
        const info = document.getElementById('terr-info');
        const lblInput = document.getElementById('terr-label-input');
        if (!info) return;
        if (S.selTerritory) {
            const t = S.territories.find(x => x.id === S.selTerritory);
            if (t) {
                info.innerHTML = `Editing: <b>${esc(t.label)}</b><br>Kind: <b>${t.kind}</b> · Size: <b>${t.size}px</b>`;
                lblInput.value = t.label;
                lblInput.style.display = 'block';
                return;
            }
        }
        info.innerHTML = `No territory selected. Tap a territory's border or name tag on the canvas, or create one above.`;
        lblInput.style.display = 'none';
    }

    // ── RENDER ───────────────────────────────────────────
    const GEN_COL = ['transparent','#2a6496','#2e7d32','#e65100','#6a1a8a'];

    function textColFor(hexColor, fontColorSetting) {
        if (fontColorSetting !== 'auto') return fontColorSetting;
        const hx = String(hexColor).replace('#','');
        const r = parseInt(hx.substr(0,2),16)||0, g = parseInt(hx.substr(2,2),16)||0, b = parseInt(hx.substr(4,2),16)||0;
        return ((r*299+g*587+b*114)/1000) >= 128 ? '#000' : '#fff';
    }

    function render() {
        const hidden = hiddenSet();

        let h = `<defs>
            <marker id="me"  markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#888"/></marker>
            <marker id="ms"  markerWidth="10" markerHeight="10" refX="1" refY="3" orient="auto"><path d="M9,0 L9,6 L0,3 z" fill="#888"/></marker>
            <marker id="mes" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#DE2626"/></marker>
            <marker id="mss" markerWidth="10" markerHeight="10" refX="1" refY="3" orient="auto"><path d="M9,0 L9,6 L0,3 z" fill="#DE2626"/></marker>
        </defs>`;

        S.territories.forEach(t => {
            const isSel = S.selTerritory === t.id;
            const box = territoryBox(t);
            const strokeW = isSel ? 4 : 2.5;
            if (t.kind === 'circle') {
                h += `<ellipse cx="${t.cx}" cy="${t.cy}" rx="${t.size/2}" ry="${t.size/2}" fill="${t.color}" opacity="0.38" style="pointer-events:none;"/>`;
                h += `<ellipse cx="${t.cx}" cy="${t.cy}" rx="${t.size/2}" ry="${t.size/2}" fill="none" stroke="${t.color}" stroke-width="${strokeW}" style="pointer-events:stroke;cursor:pointer;" onpointerdown="app.terrSelect(event,'${t.id}')"/>`;
            } else {
                h += `<rect x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" fill="${t.color}" opacity="0.38" style="pointer-events:none;"/>`;
                h += `<rect x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" fill="none" stroke="${t.color}" stroke-width="${strokeW}" style="pointer-events:stroke;cursor:pointer;" onpointerdown="app.terrSelect(event,'${t.id}')"/>`;
            }
            const lx = box.x + 8, ly = box.y + 4;
            const tagW = Math.max(40, t.label.length*7+16);
            h += `<g onpointerdown="app.terrSelect(event,'${t.id}')" style="cursor:pointer;">
                <rect x="${lx}" y="${ly}" width="${tagW}" height="20" rx="4" fill="${t.color}" opacity="0.9"/>
                <text x="${lx+8}" y="${ly+14}" font-size="11" font-weight="700" fill="#fff">${esc(t.label)}</text>
            </g>`;
        });

        S.connectors.forEach(c => {
            const s1 = S.shapes.find(x => x.id === c.from);
            const s2 = S.shapes.find(x => x.id === c.to);
            if (!s1 || !s2 || hidden.has(s1.id) || hidden.has(s2.id)) return;

            const cA = shapeCenter(s1), cB = shapeCenter(s2);
            const edgeTo   = edgePoint(cA.x, cA.y, s2);
            const edgeFrom = edgePoint(cB.x, cB.y, s1);

            const isSel = S.selConn === c.id;

            let x1 = cA.x, y1 = cA.y, x2 = edgeTo.x, y2 = edgeTo.y;
            if (c.flow === 'backward' || c.flow === 'bidirectional') { x1 = edgeFrom.x; y1 = edgeFrom.y; }

            let mk = '';
            if (c.flow === 'forward' || c.flow === 'bidirectional') mk += ` marker-end="${isSel?'url(#mes)':'url(#me)'}"`;
            if (c.flow === 'backward' || c.flow === 'bidirectional') mk += ` marker-start="${isSel?'url(#mss)':'url(#ms)'}"`;

            h += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="conn${isSel?' sel':''}" ${mk} pointer-events="none"/>`;
            h += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="conn-hit" onpointerdown="app.connDown(event,'${c.id}')"/>`;
        });

        S.shapes.forEach(s => {
            if (hidden.has(s.id)) return;
            const isSel = S.selIds.includes(s.id);
            const box   = boxOf(s);
            const memberTerr = findContainingTerritory(s);
            const fillColor = memberTerr ? memberTerr.color : s.color;
            const tc    = textColFor(fillColor, s.fontColor);
            const gen   = genOf(s.id);
            const sc    = GEN_COL[Math.min(gen, GEN_COL.length-1)];
            const kids  = childrenOf(s.id);
            const selCls = isSel ? (S.multi?'msel':'sel') : '';

            h += `<g transform="translate(${box.x},${box.y})" class="shape-group" onpointerdown="app.shapeDown(event,'${s.id}')">`;

            if (s.kind === 'circle') {
                const ringAttr = gen > 0 ? `stroke="${sc}" stroke-width="3"` : '';
                h += `<ellipse cx="${box.w/2}" cy="${box.h/2}" rx="${box.w/2}" ry="${box.h/2}" fill="${fillColor}" class="shape-rect ${selCls}" ${ringAttr}/>`;
            } else {
                h += `<rect width="${box.w}" height="${box.h}" fill="${fillColor}" class="shape-rect ${selCls}"/>`;
                if (gen > 0) h += `<rect x="0" y="0" width="${box.w}" height="5" fill="${sc}" pointer-events="none"/>`;
            }

            const tp = (gen > 0 && s.kind !== 'circle') ? 9 : 4;
            h += `<foreignObject width="${box.w}" height="${box.h}" style="pointer-events:none;overflow:hidden;">
                <div xmlns="http://www.w3.org/1999/xhtml" class="shape-text-wrap" style="color:${tc};padding-top:${tp}px;">
                    <div class="shape-label">${esc(s.label)}</div>
                    ${s.text ? `<div class="shape-body">${esc(s.text)}</div>` : ''}
                </div>
            </foreignObject>`;

            if (kids.length) {
                const BW=24, BH=20, BX=box.w-BW-3, BY=3;
                const ico = s.collapsed ? '▶' : '▼';
                const bf  = s.collapsed ? '#DE2626' : 'rgba(0,0,0,.45)';
                h += `<g onpointerdown="app.toggleCollapse(event,'${s.id}')" style="cursor:pointer;">
                    <rect x="${BX}" y="${BY}" width="${BW}" height="${BH}" rx="4" fill="${bf}" stroke="#555" stroke-width="0.5"/>
                    <text x="${BX+BW/2}" y="${BY+14}" text-anchor="middle" fill="white" font-size="10" font-family="monospace" pointer-events="none">${ico}</text>
                </g>`;
            }

            const showDot = isSel || !!S.pending;
            if (showDot) {
                const cx = box.w / 2, cy = box.h / 2;
                const isPending = S.pending?.shapeId === s.id;
                const isTarget  = S.pending && !isPending;
                const dotClass  = isPending ? ' active' : (isTarget ? ' pending-target' : '');
                h += `<circle cx="${cx}" cy="${cy}" class="anc-center-vis${dotClass}"/>`;
                h += `<circle cx="${cx}" cy="${cy}" class="anc-center-hit" onpointerdown="app.centerDown(event,'${s.id}')"/>`;
            }

            h += `</g>`;
        });

        if (S.selTerritory) {
            const t = S.territories.find(x => x.id === S.selTerritory);
            if (t) {
                // Fixed offset from the territory's CENTER (not the box edge) —
                // growing/shrinking only ever changes t.size, never t.cx/t.cy,
                // so this keeps the buttons planted under the finger across taps.
                const TOOLBAR_OFFSET = 90;
                const ty = t.cy - TOOLBAR_OFFSET;
                const tcx = t.cx;
                h += `<g>
                    <circle cx="${tcx-50}" cy="${ty}" r="16" fill="#252525" stroke="#888" onpointerdown="app.terrShrink(event)" style="cursor:pointer;"/>
                    <text x="${tcx-50}" y="${ty+5}" text-anchor="middle" fill="#fff" font-size="18" pointer-events="none">−</text>
                    <circle cx="${tcx}" cy="${ty}" r="16" fill="#252525" stroke="#888" onpointerdown="app.terrGrow(event)" style="cursor:pointer;"/>
                    <text x="${tcx}" y="${ty+5}" text-anchor="middle" fill="#fff" font-size="18" pointer-events="none">+</text>
                    <rect x="${tcx+34}" y="${ty-16}" width="32" height="32" rx="4" fill="${t.color}" stroke="#888" onpointerdown="app.openTerrColor(event,'${t.id}')" style="cursor:pointer;"/>
                </g>`;
            }
        }

        D.svg.innerHTML = h;
    }

    // ── UI UPDATE ────────────────────────────────────────
    function updateUI() {
        const n = S.selIds.length, isConn = !!S.selConn;
        document.getElementById('ctrl-none').style.display   = (!n && !isConn) ? 'block' : 'none';
        document.getElementById('ctrl-single').style.display = n === 1 ? 'block' : 'none';
        document.getElementById('ctrl-conn').style.display   = isConn ? 'block' : 'none';
        document.getElementById('btn-del').style.display     = (n > 0 || isConn || S.selTerritory) ? 'block' : 'none';

        if (n === 1) {
            const s = S.shapes.find(x => x.id === S.selIds[0]);
            if (s) {
                document.getElementById('inp-label').value = s.label;
                document.getElementById('inp-color').value = s.color;
                document.getElementById('inp-fc').value    = s.fontColor;
                document.getElementById('btn-kind-rect').style.background   = s.kind === 'rect'   ? 'var(--accent)' : '#252525';
                document.getElementById('btn-kind-circle').style.background = s.kind === 'circle' ? 'var(--accent)' : '#252525';
                const gen  = genOf(s.id);
                const kids = childrenOf(s.id);
                const hi   = document.getElementById('hier-info');
                const mkr  = document.getElementById('btn-mkroot');
                if (s.parentId) {
                    const par = S.shapes.find(x => x.id === s.parentId);
                    hi.innerHTML = `Parent: <b>${esc(par ? par.label : '?')}</b><br>Tier: <b>T${gen}</b> · Size: <b>${s.sizeClass}</b> · Children: <b>${kids.length}</b>`;
                    mkr.style.display = 'block';
                } else {
                    hi.innerHTML = `<span style="color:#aaa;">Root Node</span> · Size: <b>${s.sizeClass}</b> · Children: <b>${kids.length}</b>`;
                    mkr.style.display = 'none';
                }
                renderCustomFields(s);
            }
        }
        if (isConn) {
            const c = S.connectors.find(x => x.id === S.selConn);
            if (c) document.getElementById('inp-flow').value = c.flow || 'none';
        }
        D.stl.innerText = n > 0 ? `${n} Selected` : isConn ? 'Line Selected' : S.selTerritory ? 'Territory Selected' : 'Ready';
        D.iname.value = S.name;
    }

    function renderCustomFields(s) {
        const list = document.getElementById('cfield-list');
        const names = Object.keys(s.custom || {});
        if (!names.length) {
            list.innerHTML = `<div style="color:#666;font-size:11px;">No custom fields on this shape yet.</div>`;
            return;
        }
        list.innerHTML = names.map(name => `
            <div class="cfield-row">
                <span class="cfield-name" title="${esc(name)}">${esc(name)}</span>
                <input type="text" value="${esc(s.custom[name] ?? '')}" onchange="app.upCustomField('${name}', this.value)">
            </div>
        `).join('');
    }

    async function upCustomField(name, value) {
        if (S.selIds.length !== 1) return;
        const s = S.shapes.find(x => x.id === S.selIds[0]);
        if (!s) return;
        s.custom[name] = value;
        try { await apiSend(`/api/shape/${s.id}`, 'PUT', { customFields: [{ name, value }] }); }
        catch (e) { D.stl.innerText = 'Field save failed: ' + e.message; }
    }

    async function addCustomFieldFromUI() {
        if (S.selIds.length !== 1) return;
        const nameInput = document.getElementById('cfield-new-name');
        const typeSelect = document.getElementById('cfield-new-type');
        const name = nameInput.value.trim();
        if (!name) return;
        const type = typeSelect.value;
        const s = S.shapes.find(x => x.id === S.selIds[0]);
        if (!s) return;
        try {
            const updated = await apiSend(`/api/shape/${s.id}/field`, 'POST', { name, type, value: type === 'boolean' ? false : (type === 'number' ? 0 : '') });
            const merged = shapeFromServer(updated);
            Object.assign(s, merged);
            nameInput.value = '';
            renderCustomFields(s);
            D.stl.innerText = `Field "${name}" added`;
            setTimeout(() => { D.stl.innerText = 'Ready'; }, 1500);
        } catch (e) {
            D.stl.innerText = 'Add field failed: ' + e.message;
        }
    }

    // ── OBJECT PANEL ─────────────────────────────────────
    function toggleObjPanel() {
        S.objOpen = !S.objOpen;
        D.op.classList.toggle('open', S.objOpen);
        D.ofab.classList.toggle('lit', S.objOpen);
        if (S.objOpen) renderObj();
    }

    function renderObj() {
        if (!D.ol || !S.objOpen) return;
        const hidden = hiddenSet();
        let h = '', n = 0;

        function row(s, depth) {
            n++;
            const isSel = S.selIds.includes(s.id);
            const isH   = hidden.has(s.id);
            const kids  = childrenOf(s.id);
            const tb    = depth > 0 ? `<span class="tier-badge">T${depth}</span>` : '';
            const ci    = kids.length ? (s.collapsed ? '▶ ' : '▼ ') : '';
            const kc    = kids.length ? `<span class="obj-cnt">(${kids.length})</span>` : '';
            const ico   = s.kind === 'circle' ? '○ ' : '▭ ';
            h += `<div class="obj-item${isSel?' active':''}${isH?' hidden-node':''}"
                style="padding-left:${6+depth*13}px;"
                onpointerdown="app.selPanel(event,'${s.id}')">
                ${tb}<span class="obj-lbl">${ico}${ci}${esc(s.label)}</span>${kc}
            </div>`;
            kids.forEach(c => row(c, depth+1));
        }

        const valid = new Set(S.shapes.map(s => s.id));
        S.shapes.filter(s => !s.parentId || !valid.has(s.parentId)).forEach(s => row(s, 0));

        if (S.territories.length) {
            h += `<div class="obj-sect-hdr">Territories</div>`;
            S.territories.forEach(t => {
                n++;
                const isSel = S.selTerritory === t.id;
                h += `<div class="obj-item${isSel?' active':''}" style="padding-left:6px;"
                    onpointerdown="app.selTerrPanel(event,'${t.id}')">
                    <span class="tier-badge" style="background:${t.color};color:#fff;">●</span>
                    <span class="obj-lbl">${esc(t.label)}</span>
                </div>`;
            });
        }

        D.ol.innerHTML = h || `<div style="color:#888;padding:10px;text-align:center;font-size:12px;">No nodes</div>`;
        D.oc.textContent = `${n} node${n !== 1 ? 's' : ''}`;
    }

    function setupObjDrag() {
        const hdr = document.getElementById('obj-hdr');
        let dg = null;
        hdr.addEventListener('pointerdown', e => {
            const r = D.op.getBoundingClientRect();
            D.op.style.bottom = 'auto';
            D.op.style.top  = r.top + 'px';
            D.op.style.left = r.left + 'px';
            dg = { sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top };
            try { hdr.setPointerCapture(e.pointerId); } catch(_){}
            e.preventDefault();
        });
        hdr.addEventListener('pointermove', e => {
            if (!dg) return;
            const W = D.op.offsetWidth, H = D.op.offsetHeight;
            D.op.style.left = Math.max(0, Math.min(innerWidth - W,  dg.ox + e.clientX - dg.sx)) + 'px';
            D.op.style.top  = Math.max(0, Math.min(innerHeight - H, dg.oy + e.clientY - dg.sy)) + 'px';
        });
        hdr.addEventListener('pointerup',     () => { dg = null; });
        hdr.addEventListener('pointercancel', () => { dg = null; });
    }

    // ── MUTATIONS ────────────────────────────────────────
    async function upShape(key, val) {
        for (const id of S.selIds) {
            const s = S.shapes.find(x => x.id === id);
            if (!s) continue;
            s[key] = val;
            try { await apiSend(`/api/shape/${id}`, 'PUT', { [key]: val }); }
            catch (e) { D.stl.innerText = 'Update failed: ' + e.message; }
        }
        render(); renderObj(); updateUI();
    }
    async function upConn(key, val) {
        if (!S.selConn) return;
        const c = S.connectors.find(x => x.id === S.selConn);
        if (c) c[key] = val;
        render();
        try { await apiSend(`/api/connector/${S.selConn}`, 'PUT', { [key]: val }); }
        catch (e) { D.stl.innerText = 'Update failed: ' + e.message; }
    }
    async function setSizeClass(cls) {
        for (const id of S.selIds) {
            const s = S.shapes.find(x => x.id === id);
            if (!s) continue;
            s.sizeClass = cls;
            try { await apiSend(`/api/shape/${id}`, 'PUT', { sizeClass: cls }); }
            catch (e) { D.stl.innerText = 'Update failed: ' + e.message; }
        }
        render(); updateUI();
    }
    async function deleteSel() {
        if (S.selTerritory) {
            const id = S.selTerritory;
            S.territories = S.territories.filter(t => t.id !== id);
            S.selTerritory = null;
            updateUI(); render(); renderObj(); renderTerrTab();
            try { await apiSend(`/api/territory/${id}`, 'DELETE'); }
            catch (e) { D.stl.innerText = 'Delete failed: ' + e.message; }
            return;
        }
        if (S.selConn) {
            const id = S.selConn;
            S.connectors = S.connectors.filter(c => c.id !== id);
            S.selConn = null;
            updateUI(); render();
            try { await apiSend(`/api/connector/${id}`, 'DELETE'); }
            catch (e) { D.stl.innerText = 'Delete failed: ' + e.message; }
            return;
        }
        const ids = [...S.selIds];
        ids.forEach(did => {
            const s = S.shapes.find(x => x.id === did); if (!s) return;
            childrenOf(did).forEach(c => { c.parentId = s.parentId; });
        });
        S.connectors = S.connectors.filter(c => !ids.includes(c.from) && !ids.includes(c.to));
        S.shapes = S.shapes.filter(s => !ids.includes(s.id));
        S.selIds = [];
        updateUI(); render(); renderObj();
        for (const id of ids) {
            try { await apiSend(`/api/shape/${id}`, 'DELETE'); }
            catch (e) { D.stl.innerText = 'Delete failed: ' + e.message; }
        }
    }

    // ── SETTINGS ─────────────────────────────────────────
    function setSnap(on) {
        S.snap = on;
        document.getElementById('snap-dot').style.background = on ? 'var(--accent)' : '#888';
        D.stl.innerText = `Snap ${on ? 'ON' : 'OFF'}`;
        setTimeout(() => { D.stl.innerText = 'Ready'; }, 1500);
    }

    let nameDebounce = null;
    function setName(v) {
        S.name = v;
        clearTimeout(nameDebounce);
        nameDebounce = setTimeout(async () => {
            try { await apiSend('/api/diagram/name', 'PUT', { name: v }); }
            catch (e) { D.stl.innerText = 'Name save failed: ' + e.message; }
        }, 400);
    }

    // ── UTILS ────────────────────────────────────────────
    function esc(str) {
        return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function drawRulers() {
        D.rt.innerHTML = ''; D.rl.innerHTML = '';
        for (let i = 0; i < 2000; i += 100) {
            const t = document.createElement('div'); t.className = 'tick tick-h'; t.style.left = (i*S.zoom)+'px'; t.innerText = i; D.rt.appendChild(t);
            const l = document.createElement('div'); l.className = 'tick tick-v'; l.style.top  = (i*S.zoom)+'px'; l.innerText = i; D.rl.appendChild(l);
        }
    }
    function updateRulers() {
        D.rt.style.left = (-D.vp.scrollLeft) + 'px';
        D.rl.style.top  = (-D.vp.scrollTop)  + 'px';
    }

    // ── LIGHTROOM ────────────────────────────────────────
    let lrMode = null;
    function openTextEditor() {
        if (S.selIds.length !== 1) return;
        lrMode = 'text'; D.lrt.innerText = 'Edit Body Text';
        D.lri.style.display = 'block'; D.lrv.style.display = 'none';
        D.lri.value = S.shapes.find(x => x.id === S.selIds[0])?.text || '';
        D.lr.classList.add('open');
    }
    async function openSchemaView() {
        lrMode = 'schema'; D.lrt.innerText = 'Live Schema (MFDB)';
        D.lri.style.display = 'none'; D.lrv.style.display = 'block';
        D.lrv.textContent = 'Loading…';
        D.lr.classList.add('open');
        try {
            const schema = await apiGet('/api/schema');
            D.lrv.textContent = JSON.stringify(schema, null, 2);
        } catch (e) {
            D.lrv.textContent = 'Failed to load schema: ' + e.message;
        }
    }
    async function closeLightroom() {
        if (lrMode === 'text') {
            const s = S.shapes.find(x => x.id === S.selIds[0]);
            if (s) {
                s.text = D.lri.value;
                render();
                try { await apiSend(`/api/shape/${s.id}`, 'PUT', { text: s.text }); }
                catch (e) { D.stl.innerText = 'Save failed: ' + e.message; }
            }
        }
        D.lr.classList.remove('open'); lrMode = null;
    }

    async function exportMfdb() {
        D.stl.innerText = 'Exporting…';
        window.location.href = '/api/export';
        setTimeout(() => { D.stl.innerText = 'Ready'; }, 1500);
    }

    async function importMfdbFile(file) {
        if (!file) return;
        D.stl.innerText = 'Importing…';
        const fd = new FormData();
        fd.append('file', file);
        try {
            const r = await fetch('/api/import', { method: 'POST', body: fd });
            if (!r.ok) {
                const err = await r.json().catch(() => ({ error: r.statusText }));
                throw new Error(err.error || `Import -> ${r.status}`);
            }
            const res = await r.json();
            applyDiagramPayload(res.diagram);
            render(); renderObj(); updateUI();
            await loadRecentDiagramsList();
            D.stl.innerText = 'Ready';
        } catch (e) {
            D.stl.innerText = 'Import failed: ' + e.message;
        } finally {
            document.getElementById('import-file-input').value = '';
        }
    }

    // ── PUBLIC API ───────────────────────────────────────
    return {
        init, addShape, addChildNode, makeRoot,
        toggleCollapse, selPanel: selFromPanel,
        shapeDown:  onShapeDown,
        centerDown: onCenterDown,
        connDown:   onConnDown,
        addTerritory, terrSelect, terrGrow, terrShrink,
        openTerrColor, setTerrColorFromPicker, setTerrLabel, selTerrPanel, switchTab,
        toggleDrawer:  () => D.drw.classList.toggle('open'),
        toggleFab:     () => D.fab.classList.toggle('open'),
        toggleObjPanel,
        toggleTheme: () => document.body.classList.toggle('light'),
        showAbout: () => { document.getElementById('about-modal').style.display = 'flex'; },
        zoom: d => {
            S.zoom = Math.max(.2, Math.min(3, S.zoom + d));
            D.cnt.style.transform = `scale(${S.zoom})`;
            drawRulers(); D.str.innerText = Math.round(S.zoom * 100) + '%';
        },
        setSnap, setName,
        upShape, upConn, setSizeClass, deleteSel,
        upCustomField, addCustomFieldFromUI,
        openTextEditor, openSchemaView, closeLightroom, exportMfdb, importMfdbFile,
        newDiagramPrompt, loadDiagramFromSelect, saveDiagram
    };
})();

document.addEventListener('DOMContentLoaded', () => app.init());
