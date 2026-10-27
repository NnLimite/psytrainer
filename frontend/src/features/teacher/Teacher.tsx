import { useEffect, useState } from "react";
import { api, del, errMsg, patch, post, put } from "../../api/client";
import { useAuth } from "../auth/AuthContext";

type Group = { id: string; name: string; members_count: number };
type Member = { user_id: string; full_name: string; email: string; role: string };
type Summary = { sessions_count: number; active_students: number; avg_total_score: number | null; open_safety_events: number; skills: { code: string; title: string; avg_score: number }[] };
type Event = { id: string; session_id: string; event_type: string; severity: string; status: string; created_at: string };
const TYPES: Record<string, string> = { student_risk: "Признаки кризиса у студента", role_break: "Пациент вышел из роли", harmful_content: "Вредный контент" };

export default function Teacher() {
  const { user } = useAuth();
  const [tab, setTab] = useState<"groups" | "analytics" | "safety" | "members">("groups");
  const [groups, setGroups] = useState<Group[]>([]);
  const [gid, setGid] = useState("");
  const [students, setStudents] = useState<Member[]>([]);
  const [roster, setRoster] = useState<Member[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [name, setName] = useState("");
  const [inv, setInv] = useState({ email: "", role: "student" });
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => { try { setMsg(null); await fn(); } catch (e) { setMsg(errMsg(e)); } };
  const loadGroups = () => run(async () => { const p = await api<{ items: Group[] }>("/groups?limit=100"); setGroups(p.items); setGid((g) => g || p.items[0]?.id || ""); });
  const loadRoster = () => run(async () => setRoster((await api<{ items: Member[] }>(`/groups/${gid}/members?limit=100`)).items));
  useEffect(() => { loadGroups(); }, []);
  useEffect(() => { if (user?.org_id) api<{ items: Member[] }>(`/organizations/${user.org_id}/members?limit=100`).then((p) => setStudents(p.items)).catch(() => {}); }, [user]);
  useEffect(() => { if (gid) { loadRoster(); if (tab === "analytics") run(async () => setSummary(await api(`/analytics/groups/${gid}/summary`))); } }, [gid, tab]);
  useEffect(() => { if (tab === "safety") run(async () => setEvents((await api<{ items: Event[] }>("/safety-events?status=open&limit=50")).items)); }, [tab]);

  const tabs: [typeof tab, string][] = [["groups", "Группы"], ["analytics", "Аналитика"], ["safety", "Безопасность"], ...(user?.role === "org_admin" ? [["members", "Участники"] as [typeof tab, string]] : [])];
  return (
    <section>
      <h1>Кабинет</h1>
      <div className="tabs">{tabs.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab !== "safety" && tab !== "members" && <select value={gid} onChange={(e) => setGid(e.target.value)}>{groups.map((g) => <option key={g.id} value={g.id}>{g.name} ({g.members_count})</option>)}</select>}
      {tab === "groups" && <>
        <div className="panel row"><input placeholder="Название новой группы" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" onClick={() => run(async () => { await post("/groups", { org_id: user!.org_id, name }); setName(""); await loadGroups(); })}>Создать</button></div>
        {gid && <div className="panel"><h3>Состав</h3>
          {roster.map((m) => <p className="row" key={m.user_id}>{m.full_name} <span className="muted">{m.email}</span>
            <button className="ghost" onClick={() => run(async () => { await del(`/groups/${gid}/members/${m.user_id}`); await loadRoster(); await loadGroups(); })}>Убрать</button></p>)}
          <select value="" onChange={(e) => run(async () => { await put(`/groups/${gid}/members/${e.target.value}`); await loadRoster(); await loadGroups(); })}>
            <option value="">Добавить студента…</option>{students.filter((s) => s.role === "student" && !roster.some((r) => r.user_id === s.user_id)).map((s) => <option key={s.user_id} value={s.user_id}>{s.full_name} ({s.email})</option>)}</select></div>}</>}
      {tab === "analytics" && summary && <div className="panel">
        <p>Сессий: <b>{summary.sessions_count}</b> · активных студентов: <b>{summary.active_students}</b> · средний балл: <b>{summary.avg_total_score ?? "—"}</b> · открытых событий безопасности: <b>{summary.open_safety_events}</b></p>
        <p className="muted">В аналитику попадают только сессии студентов, давших согласие на аналитику.</p>
        {summary.skills.map((s) => <div className="bar" key={s.code}>{s.title}: {s.avg_score} из 4<div className="track"><i data-k="trust" style={{ width: `${s.avg_score * 25}%` }} /></div></div>)}</div>}
      {tab === "safety" && <div className="panel">{events.length === 0 && <p className="muted">Открытых событий нет.</p>}
        {events.map((e) => <p className="row" key={e.id}><b>{TYPES[e.event_type] ?? e.event_type}</b> <span className={e.severity === "high" ? "error" : "muted"}>{e.severity}</span>
          <span className="muted">{new Date(e.created_at).toLocaleString()}</span>
          <button className="ghost" onClick={() => run(async () => { await patch(`/safety-events/${e.id}`, { status: "resolved" }); setEvents(events.filter((x) => x.id !== e.id)); })}>Закрыть</button></p>)}</div>}
      {tab === "members" && <div className="panel"><h3>Участники организации</h3>
        {students.map((m) => <p key={m.user_id}>{m.full_name} <span className="muted">{m.email} · {m.role}</span></p>)}
        <div className="row"><input placeholder="Email приглашаемого" value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} />
          <select value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}><option value="student">студент</option><option value="teacher">преподаватель</option></select>
          <button className="primary" onClick={() => run(async () => { await post(`/organizations/${user!.org_id}/invitations`, inv); setMsg("Приглашение отправлено"); setInv({ ...inv, email: "" }); })}>Пригласить</button></div></div>}
      {msg && <p className="notice" role="status">{msg}</p>}
    </section>
  );
}
