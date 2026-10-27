import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { errMsg } from "../../api/client";
import { useAuth } from "./AuthContext";

export default function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { login, register } = useAuth();
  const nav = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/app";
  const invitation = useSearchParams()[0].get("invitation") ?? undefined; // ссылка из письма-приглашения
  const [f, setF] = useState({ email: "", password: "", name: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { mode === "login" ? await login(f.email, f.password) : await register(f.email, f.password, f.name, invitation); nav(from, { replace: true }); }
    catch (x) { setErr(errMsg(x)); } finally { setBusy(false); }
  };
  return (
    <main className="auth"><form className="panel" onSubmit={submit}>
      <h1>{mode === "login" ? "Вход" : "Создать аккаунт"}</h1>
      {mode === "register" && <label>Имя<input required value={f.name} onChange={set("name")} autoComplete="name" /></label>}
      <label>Email<input required type="email" value={f.email} onChange={set("email")} autoComplete="email" /></label>
      <label>Пароль{mode === "register" && <small> не короче 10 символов</small>}
        <input required minLength={mode === "register" ? 10 : 1} type="password" value={f.password} onChange={set("password")}
          autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
      {err && <p className="error" role="alert">{err}</p>}
      <button className="primary" disabled={busy}>{busy ? "…" : mode === "login" ? "Войти" : "Зарегистрироваться"}</button>
      <p className="muted">{mode === "login" ? <>Нет аккаунта? <Link to="/register">Регистрация</Link></> : <>Уже есть аккаунт? <Link to="/login">Войти</Link></>}</p>
    </form></main>
  );
}
