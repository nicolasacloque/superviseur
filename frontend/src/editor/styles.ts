const STYLE_ID = 'syn-editor-styles'

export function injectEditorStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
.ed [hidden],.ed-modal [hidden]{display:none!important}
.ed{display:grid;grid-template-columns:150px 1fr 300px;grid-template-rows:auto 1fr auto;grid-template-areas:"bar bar bar" "pal view prop" "stat stat stat";height:100%;background:#0b1220;color:#e2e8f0;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.ed button,.ed input,.ed select{font:inherit}
.ed button{padding:5px 10px;border:1px solid #334155;border-radius:6px;background:#1e293b;color:inherit;cursor:pointer}
.ed button:hover:not(:disabled){background:#334155}
.ed button:disabled{opacity:.5;cursor:default}
.ed button.primary{background:#2563eb;border-color:#2563eb}
.ed button.danger{border-color:#7f1d1d;color:#fca5a5}
.ed button:focus-visible,.ed input:focus-visible,.ed select:focus-visible{outline:2px solid #93c5fd;outline-offset:1px}
.ed input[type=text],.ed input[type=number],.ed input[type=search],.ed select{width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid #334155;border-radius:6px;background:#0f172a;color:inherit}
.ed input[aria-invalid="true"]{border-color:#ef4444}
.ed-toolbar{grid-area:bar;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;background:#111827;border-bottom:1px solid #1f2937}
.ed-toolbar .ed-name{width:220px;font-weight:600}
.ed-check{display:flex;align-items:center;gap:4px;color:#cbd5e1}
.ed-zoom{min-width:48px;text-align:center;color:#cbd5e1}
.ed-palette{grid-area:pal;overflow:auto;padding:8px;background:#111827;border-right:1px solid #1f2937;display:flex;flex-direction:column;gap:6px}
.ed-palette h2{margin:0 0 4px;font-size:13px;color:#94a3b8;font-weight:600}
.ed-palette__item{text-align:left}
.ed-palette.is-disabled{opacity:.5;pointer-events:none}
.ed-viewport{grid-area:view;position:relative;overflow:auto;background:#020617;outline:none}
.ed-stage{position:relative;margin:16px}
.ed-canvas{position:absolute;left:0;top:0;transform-origin:0 0;user-select:none;touch-action:none;box-shadow:0 0 0 1px #334155}
.ed-grid-layer{position:absolute;inset:0;pointer-events:none;background-image:linear-gradient(to right,rgba(148,163,184,.18) 1px,transparent 1px),linear-gradient(to bottom,rgba(148,163,184,.18) 1px,transparent 1px)}
.ed-grid-layer.on-light{background-image:linear-gradient(to right,rgba(15,23,42,.14) 1px,transparent 1px),linear-gradient(to bottom,rgba(15,23,42,.14) 1px,transparent 1px)}
.ed-item{position:absolute;box-sizing:border-box}
.ed-item__content{position:absolute;inset:0;pointer-events:none}
.ed-hit{position:absolute;inset:0;cursor:move}
.ed-item:hover .ed-hit{outline:1px dashed rgba(147,197,253,.7)}
.ed-item.is-selected .ed-hit{outline:2px solid #60a5fa}
.ed-selection-layer{position:absolute;inset:0;pointer-events:none}
.ed-selbox{position:absolute;box-sizing:border-box;border:1px dashed #60a5fa}
.ed-handle{position:absolute;width:10px;height:10px;box-sizing:border-box;background:#fff;border:2px solid #2563eb;border-radius:2px;pointer-events:auto}
.ed-handle--nw{left:-6px;top:-6px;cursor:nwse-resize}.ed-handle--n{left:calc(50% - 5px);top:-6px;cursor:ns-resize}.ed-handle--ne{right:-6px;top:-6px;cursor:nesw-resize}.ed-handle--e{right:-6px;top:calc(50% - 5px);cursor:ew-resize}.ed-handle--se{right:-6px;bottom:-6px;cursor:nwse-resize}.ed-handle--s{left:calc(50% - 5px);bottom:-6px;cursor:ns-resize}.ed-handle--sw{left:-6px;bottom:-6px;cursor:nesw-resize}.ed-handle--w{left:-6px;top:calc(50% - 5px);cursor:ew-resize}
.ed-band{position:absolute;border:1px solid #60a5fa;background:rgba(96,165,250,.15);pointer-events:none}
.ed-preview{position:absolute;inset:0}
.ed-panel{grid-area:prop;overflow:auto;padding:8px 12px;background:#111827;border-left:1px solid #1f2937}
.ed-section{margin-bottom:14px}
.ed-section h3{margin:0 0 6px;font-size:13px;color:#94a3b8;font-weight:600}
.ed-row{display:flex;flex-direction:column;gap:2px;margin-bottom:8px}
.ed-row__label{font-size:12px;color:#cbd5e1}
.ed-row__hint{font-size:12px;color:#94a3b8}
.ed-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.ed-cell{display:flex;flex-direction:column;font-size:12px;color:#cbd5e1;gap:2px}
.ed-buttons{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}
.ed-color,.ed-point{display:flex;gap:6px;align-items:center}
.ed-color input[type=color]{width:44px;height:28px;padding:0;border:1px solid #334155;background:none}
.ed-point button:first-child{flex:1;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ed-rule{padding:8px;margin-bottom:8px;border:1px solid #1f2937;border-radius:6px}
.ed-points{display:flex;flex-direction:column;gap:6px;margin-bottom:8px}
.ed-point-row{display:grid;grid-template-columns:1fr 90px auto;gap:4px;align-items:center}
.ed-point-row .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.ed-status{grid-area:stat;padding:6px 12px;background:#111827;border-top:1px solid #1f2937;color:#cbd5e1;font-size:13px;min-height:1.4em}
.ed-status:empty::before{content:'Prêt'}
.ed-modal{position:fixed;inset:0;z-index:900;display:flex;align-items:center;justify-content:center;background:rgba(2,6,23,.65)}
.ed-modal__box{display:flex;flex-direction:column;gap:8px;width:min(92vw,560px);max-height:80vh;padding:16px;border-radius:10px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;font:14px/1.4 system-ui,sans-serif}
.ed-modal__box h2{margin:0;font-size:16px}
.ed-modal__box input{padding:6px 8px;border:1px solid #334155;border-radius:6px;background:#020617;color:inherit;font:inherit}
.ed-modal__box button{padding:5px 10px;border:1px solid #334155;border-radius:6px;background:#1e293b;color:inherit;cursor:pointer;font:inherit}
.ed-modal__box button:focus-visible,.ed-modal__box input:focus-visible{outline:2px solid #93c5fd;outline-offset:1px}
.ed-picker__crumbs{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.ed-picker__crumbs .sep{color:#64748b}
.ed-picker__list,.ed-versions__list{list-style:none;margin:0;padding:0;overflow:auto;min-height:120px}
.ed-picker__list li button{display:grid;grid-template-columns:1.3fr 2fr auto;gap:8px;width:100%;text-align:left;margin-bottom:4px}
.ed-picker__list .path,.ed-picker__list .count{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ed-picker__list .value{font-weight:600}
.ed-picker__status{min-height:1.4em;color:#94a3b8}
.ed-versions__list li{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid #1f2937}
.ed-versions__list .badge{padding:2px 8px;border-radius:10px;background:#1e3a8a;font-size:12px}
`
  document.head.append(style)
}
