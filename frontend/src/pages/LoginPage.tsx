import { FormEvent, useState } from "react";
import { api, ApiError } from "../api/client";
import type { AuthSession } from "../api/types";
import { ThemeSwitcher } from "../components/ThemeSwitcher";

export function LoginPage({ onLogin }: { onLogin: (session: AuthSession) => void }) {
  const [username, setUsername] = useState("amigo");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(username, password));
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 401
        ? "Неверное имя пользователя или пароль."
        : "Не удалось выполнить вход. Попробуйте ещё раз.");
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card">
        <div className="login-card__top">
          <div className="brand brand--static"><span className="brand__mark">A</span><span><strong>Amigo</strong><small>Личный дневник здоровья</small></span></div>
          <ThemeSwitcher />
        </div>
        <div className="login-card__intro">
          <span className="eyebrow">Защищённый доступ</span>
          <h1>Вход в Amigo</h1>
          <p>Медицинские показатели, документы и переписка доступны только после входа.</p>
        </div>
        <form className="form-stack" onSubmit={submit}>
          <label>Имя пользователя<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
          <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus /></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="button button--primary" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
        </form>
      </section>
    </main>
  );
}
