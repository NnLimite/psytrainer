import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errMsg, post, streamMessage, type State } from "../../api/client";

type Msg = { from: "student" | "patient"; text: string };
type Session = { id: string; status: "active" | "ended"; mode: string; state: State | null };
const BARS: [keyof State, string][] = [["trust", "Доверие"], ["anxiety", "Тревога"], ["resistance", "Сопротивление"], ["openness", "Открытость"]];

export default function SessionPage() {
  const { id } = useParams();
  const [s, setS] = useState<Session | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [state, setState] = useState<State | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      const ses = await api<Session>(`/sessions/${id}`);
      setS(ses); setState(ses.state);
      const t = await api<{ items: { speaker: Msg["from"]; content: string }[] }>(`/sessions/${id}/turns?limit=100`);
      setMsgs(t.items.map((x) => ({ from: x.speaker, text: x.content })));
    })().catch((e) => setErr(errMsg(e)));
  }, [id]);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [msgs]);

  const send = async () => {
    const t = text.trim();
    if (!t || busy || s?.status !== "active") return;
    setText(""); setErr(null); setBusy(true);
    setMsgs((m) => [...m, { from: "student", text: t }, { from: "patient", text: "" }]);
    const dropPlaceholder = () => setMsgs((m) => m.slice(0, -1));
    try {
      for await (const ev of streamMessage(id!, t)) {
        if (ev.type === "token") setMsgs((m) => m.map((x, i) => (i === m.length - 1 ? { ...x, text: x.text + ev.text } : x)));
        else if (ev.type === "done") { if (ev.state) setState(ev.state); if (ev.notice) setNotice(ev.notice.text); }
        else if (ev.type === "ended") { dropPlaceholder(); setS((p) => p && { ...p, status: "ended" }); }
        else { dropPlaceholder(); if (ev.type === "error") setErr(ev.message); }
      }
    } catch (e) { dropPlaceholder(); setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const end = async () => setS(await post<Session>(`/sessions/${id}/end`).catch((e) => { setErr(errMsg(e)); return s; }));

  if (!s) return <p className="center">{err ?? "Загрузка…"}</p>;
  return (
    <div className="session">
      <section className="panel chatbox">
        <div className="chat" ref={box} aria-live="polite">
          {msgs.map((m, i) => <p key={i} className={`msg ${m.from}`}>{m.text || "…"}</p>)}
        </div>
        {notice && <p className="notice" role="alert">{notice}</p>}
        {err && <p className="error" role="alert">{err}</p>}
        {s.status === "active" ? (
          <div className="composer">
            <textarea rows={2} value={text} placeholder="Ваша реплика (Enter — отправить)" onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button className="primary" disabled={busy} onClick={send}>Отправить</button>
          </div>
        ) : <p>Сессия завершена. <Link to={`/app/session/${id}/debrief`}>Перейти к разбору</Link></p>}
      </section>
      <aside className="panel">
        <h3>Состояние пациента</h3>
        {state ? BARS.map(([k, label]) => (
          <div className="bar" key={k}><span>{label}</span><div className="track"><i data-k={k} style={{ width: `${state[k] * 100}%` }} /></div></div>
        )) : <p className="muted">В этом режиме шкалы скрыты.</p>}
        {s.status === "active" && <button className="ghost" onClick={end}>Завершить сессию</button>}
      </aside>
    </div>
  );
}
