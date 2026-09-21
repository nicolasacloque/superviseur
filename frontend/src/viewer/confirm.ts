/** Confirmation modale accessible avant une commande (remplace `window.confirm`). */
export function dialogConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const previous = document.activeElement as HTMLElement | null
    const overlay = document.createElement('div')
    overlay.className = 'syn-confirm'
    overlay.setAttribute('role', 'alertdialog')
    overlay.setAttribute('aria-modal', 'true')
    overlay.setAttribute('aria-label', 'Confirmation')
    const box = document.createElement('div')
    box.className = 'syn-confirm__box'
    const text = document.createElement('p')
    text.textContent = message
    const actions = document.createElement('div')
    const cancel = document.createElement('button')
    cancel.type = 'button'
    cancel.textContent = 'Annuler'
    const ok = document.createElement('button')
    ok.type = 'button'
    ok.textContent = 'Confirmer'
    ok.className = 'primary'
    actions.append(cancel, ok)
    box.append(text, actions)
    overlay.append(box)

    const close = (answer: boolean) => {
      overlay.remove()
      document.removeEventListener('keydown', onKey, true)
      previous?.focus?.()
      resolve(answer)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close(false)
      } else if (event.key === 'Tab') {
        // Le focus reste dans la boîte de dialogue.
        event.preventDefault()
        ;(document.activeElement === ok ? cancel : ok).focus()
      }
    }
    cancel.addEventListener('click', () => close(false))
    ok.addEventListener('click', () => close(true))
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close(false)
    })
    document.addEventListener('keydown', onKey, true)
    document.body.append(overlay)
    cancel.focus() // par défaut, le choix prudent
  })
}

const STYLE_ID = 'syn-confirm-styles'

export function injectConfirmStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
.syn-confirm{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;background:rgba(2,6,23,.6)}
.syn-confirm__box{max-width:min(90vw,420px);padding:20px;border-radius:10px;background:#0f172a;color:#fff;border:1px solid #334155;font:15px/1.4 system-ui,sans-serif}
.syn-confirm__box p{margin:0 0 16px}
.syn-confirm__box div{display:flex;justify-content:flex-end;gap:8px}
.syn-confirm__box button{padding:8px 16px;border-radius:6px;border:1px solid #475569;background:transparent;color:inherit;font:inherit;cursor:pointer}
.syn-confirm__box button.primary{background:#2563eb;border-color:#2563eb}
.syn-confirm__box button:focus-visible{outline:2px solid #fff;outline-offset:2px}
`
  document.head.append(style)
}
