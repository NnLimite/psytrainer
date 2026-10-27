import { useEffect, useState } from "react";
import { api, del, download, errMsg, post, put } from "../../api/client";
import { useAuth } from "../auth/AuthContext";

type Consent = { kind: string; granted: boolean };
const TEXT: Record<string, string> = {
  transcript_processing: "Хранить и обрабатывать транскрипты моих тренировок (нужно для работы тренажёра)",
  analytics: "Учитывать мои сессии в аналитике группы",
  research_use: "Использовать обезличенные данные в исследованиях",
};

export default function Settings() {
  const { user, logout } = useAuth();
  const [consents, setConsents] = useState<Consent[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { api<Consent[]>("/me/consents").then(setConsents).catch((e) => setMsg(errMsg(e))); }, []);

  const toggle = async (c: Consent) => {
    try { const r = await put<Consent>(`/me/consents/${c.kind}`, { granted: !c.granted }); setConsents((l) => l.map((x) => (x.kind === r.kind ? r : x))); }
    catch (e) { setMsg(errMsg(e)); }
  };
  const job = async (start: () => Promise<{ id: string }>) => {
    const { id } = await start();
    for (let j = await api<{ status: string }>(`/jobs/${id}`); ["queued", "running"].includes(j.status); j = await api(`/jobs/${id}`)) await new Promise((r) => setTimeout(r, 1500));
    return id;
  };
  const exportData = async () => {
    setMsg("Готовим выгрузку…");
    try { await download(`/jobs/${await job(() => post("/me/data-export"))}/download`, "psytrainer-data.zip"); setMsg(null); } catch (e) { setMsg(errMsg(e)); }
  };
  const remove = async () => {
    if (!confirm("Аккаунт, транскрипты и формулировки будут удалены без возможности восстановления. Продолжить?")) return;
    try { await del("/me"); await logout(); } catch (e) { setMsg(errMsg(e)); }
  };
  return (
    <section className="narrow">
      <h1>Настройки</h1>
      <p className="muted">{user?.full_name} · {user?.email}</p>
      <div className="panel"><h3>Согласия</h3>
        {consents.map((c) => <label className="check" key={c.kind}><input type="checkbox" checked={c.granted} onChange={() => toggle(c)} />{TEXT[c.kind]}</label>)}</div>
      <div className="panel"><h3>Мои данные</h3>
        <button className="ghost" onClick={exportData}>Скачать мои данные (ZIP)</button>
        <button className="danger" onClick={remove}>Удалить аккаунт и данные</button></div>
      {msg && <p className="muted" role="status">{msg}</p>}
    </section>
  );
}
