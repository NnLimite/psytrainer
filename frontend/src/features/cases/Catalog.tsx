import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError, api, errMsg, post, put } from "../../api/client";

type Case = { id: string; title: string; disorders: { code: string; name: string }[] };

export default function Catalog() {
  const nav = useNavigate();
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Case[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => api<{ items: Case[] }>(`/cases?limit=50&q=${encodeURIComponent(q)}`).then((p) => setItems(p.items)).catch((e) => setErr(errMsg(e))), 250);
    return () => clearTimeout(t);
  }, [q]);

  const start = async (c: Case) => {
    const open = () => post<{ id: string }>("/sessions", { case_id: c.id, mode: "training" });
    try {
      try { nav(`/app/session/${(await open()).id}`); }
      catch (e) {
        if (!(e instanceof ApiError && e.code === "consent_required")) throw e;
        if (!confirm("Для тренировок нужно согласие на хранение и обработку транскриптов ваших диалогов. Отозвать его и удалить данные можно в настройках. Согласны?")) return;
        await put("/me/consents/transcript_processing", { granted: true });
        nav(`/app/session/${(await open()).id}`);
      }
    } catch (e) { setErr(errMsg(e)); }
  };
  return (
    <section>
      <h1>Пациенты</h1>
      <input className="search" placeholder="Поиск по названию" value={q} onChange={(e) => setQ(e.target.value)} />
      {err && <p className="error" role="alert">{err}</p>}
      {items?.length === 0 && <p className="muted">Пока нет доступных пациентов.</p>}
      <div className="cards">{items?.map((c) => (
        <article className="panel" key={c.id}><h3>{c.title}</h3>
          <p className="muted">{c.disorders.map((d) => d.name).join(", ") || "Описание скрыто: вы определяете случай сами"}</p>
          <button className="primary" onClick={() => start(c)}>Начать сессию</button>{user?.role !== "student" && <button className="ghost" onClick={() => nav(`/app/cases/${c.id}`)}>Редактировать</button>}</article>
      ))}</div>
    </section>
  );
}
