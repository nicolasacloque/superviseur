import { ApiError } from '../api/client'
import { element } from '../widgets/base'

export interface LoginDeps {
  login(login: string, password: string): Promise<void>
  onLoggedIn(): void
}

export function mountLogin(container: HTMLElement, deps: LoginDeps): { destroy(): void } {
  const form = element('form', 'login')
  const title = element('h1', '', 'Connexion')
  const error = element('p', 'error')
  error.setAttribute('role', 'alert')
  const field = (label: string, type: string, name: string, autocomplete: string) => {
    const wrap = element('label', '', label)
    const input = element('input')
    input.type = type
    input.name = name
    input.required = true
    input.autocomplete = autocomplete as AutoFill
    wrap.append(input)
    return { wrap, input }
  }
  const user = field('Identifiant', 'text', 'login', 'username')
  const password = field('Mot de passe', 'password', 'password', 'current-password')
  const submit = element('button', 'primary', 'Se connecter')
  submit.type = 'submit'
  form.append(title, error, user.wrap, password.wrap, submit)
  form.addEventListener('submit', (event) => {
    event.preventDefault()
    submit.disabled = true
    error.textContent = ''
    deps
      .login(user.input.value.trim(), password.input.value)
      .then(() => deps.onLoggedIn())
      .catch((failure: unknown) => {
        error.textContent =
          failure instanceof ApiError && failure.status === 429
            ? 'Trop de tentatives : réessayez dans quelques minutes.'
            : failure instanceof ApiError && failure.status === 401
              ? 'Identifiant ou mot de passe incorrect.'
              : 'Connexion impossible.'
        password.input.value = ''
        password.input.focus()
      })
      .finally(() => {
        submit.disabled = false
      })
  })
  container.append(form)
  user.input.focus()
  return { destroy: () => form.remove() }
}
