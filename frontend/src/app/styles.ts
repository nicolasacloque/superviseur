const STYLE_ID = 'syn-app-styles'

export function injectAppStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
html,body{height:100%;margin:0}
body{background:#0b1220;color:#e2e8f0;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
#app{height:100%}
.app{display:flex;flex-direction:column;height:100%}
.app button,.app input{font:inherit}
.app button,.app a.btn{display:inline-block;padding:5px 12px;border:1px solid #334155;border-radius:6px;background:#1e293b;color:inherit;cursor:pointer;text-decoration:none}
.app button:hover:not(:disabled),.app a.btn:hover{background:#334155}
.app button.primary,.app a.btn.primary{background:#2563eb;border-color:#2563eb}
.app button.danger{border-color:#7f1d1d;color:#fca5a5}
.app button:focus-visible,.app a:focus-visible,.app input:focus-visible{outline:2px solid #93c5fd;outline-offset:2px}
.app-bar{display:flex;align-items:center;gap:12px;padding:8px 16px;background:#111827;border-bottom:1px solid #1f2937}
.app-bar h1{margin:0;font-size:16px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.app-bar .who{color:#94a3b8}
.app-body{flex:1;min-height:0;position:relative}
.page{max-width:900px;margin:0 auto;padding:16px}
.login{max-width:340px;margin:12vh auto 0;padding:24px;border:1px solid #1f2937;border-radius:10px;background:#111827}
.login h1{margin:0 0 16px;font-size:18px}
.login label{display:flex;flex-direction:column;gap:4px;margin-bottom:12px;color:#cbd5e1}
.login input{padding:6px 8px;border:1px solid #334155;border-radius:6px;background:#0f172a;color:inherit}
.login .error,.list__error{color:#fca5a5;min-height:1.4em;margin:0 0 8px}
.list__head{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.list__head h2{margin:0;font-size:18px}
.list__items{list-style:none;margin:0;padding:0}
.list__items li{display:flex;align-items:center;gap:8px;padding:10px 12px;border:1px solid #1f2937;border-radius:8px;margin-bottom:8px;background:#111827}
.list__items .name{flex:1;font-weight:600}
.list__items .meta{color:#94a3b8;font-size:12px}
.list__empty{color:#94a3b8}
.app-note{padding:24px;color:#cbd5e1}
@media (max-width:640px){.app-bar{flex-wrap:wrap}.list__items li{flex-wrap:wrap}}
`
  document.head.append(style)
}
