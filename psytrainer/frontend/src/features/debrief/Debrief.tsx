import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errMsg, post, waitJob } from "../../api/client";

type Score = { criterion_code: string; score: number; evidence_turn_id: string | null; comment: string | null };
type Assessment = { id: string; total_score: number; summary: string; teacher_confirmed: boolean; assessor_type: string; scores: Score[] };
type Rubric = { id: string; name: string; version: number; criteria: { code: string; title: string }[] };
type Turn = { id: string; turn_no: number; content: string };

export default function Debrief() {
  const { id } = useParams();
  const [list, setList] = useState<Assessment[]>([]);
  const [rubrics, setRubrics] = useState<Rubric[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [match, setMatch] = useState<number | null>(null);
  const [rubric, setRubric] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api<Assessment[]>(`/sessions/${id}/assessments`).then(setList).catch((e) => setMsg(errMsg(e)));
  useEffect(() => {
    load();
    api<{ items: Rubric[] }>("/rubrics?limit=50").then((p) => { setRubrics(p.items); setRubric(p.items[0]?.id ?? ""); }).catch(() => {});
    api<{ items: Turn[] }>(`/sessions/${id}/turns?limit=100`).then((p) => setTurns(p.items)).catch(() => {});
    api<{ match_score: number | null }>(`/sessions/${id}/formulation`).then((f) => setMatch(f.match_score)).catch(() => {});
  }, [id]);

  const run = async () => {
    setMsg("Идёт оценка: это занимает до минуты…");
    try {
      const job = await post<{ id: string }>(`/sessions/${id}/assessments`, { rubric_id: rubric });
      const done = await waitJob(job.id);
      setMsg(done.status === "succeeded" ? null : `Оценка не удалась: ${done.error ?? ""}`);
      load();
    } catch (e) { setMsg(errMsg(e)); }
  };
  const titles = Object.fromEntries(rubrics.flatMap((r) => r.criteria.map((c) => [c.code, c.title])));
  const quote = (tid: string | null) => turns.find((t) => t.id === tid);

  return (
    <section className="narrow">
      <h1>Разбор сессии</h1>
      {match !== null && <p className="muted">Совпадение вашей модели случая с эталоном: {Math.round(match * 100)}% (грубая лексическая оценка)</p>}
      {list.map((a) => (
        <div className="panel" key={a.id}>
          <h2>{Math.round(a.total_score)} / 100 {a.teacher_confirmed && <small className="muted">· подтверждено преподавателем</small>}</h2>
          <p>{a.summary}</p>
          {a.scores.map((s) => (
            <div className="bar" key={s.criterion_code}><b>{titles[s.criterion_code] ?? s.criterion_code}: {s.score} из 4</b>
              <div className="track"><i data-k="trust" style={{ width: `${s.score * 25}%` }} /></div>
              {s.comment && <small className="muted">{s.comment}</small>}
              {quote(s.evidence_turn_id) && <blockquote>реплика {quote(s.evidence_turn_id)!.turn_no}: «{quote(s.evidence_turn_id)!.content}»</blockquote>}
            </div>
          ))}
        </div>
      ))}
      {list.length === 0 && (
        <div className="panel"><h3>Получить оценку</h3>
          {rubrics.length === 0 ? <p className="muted">Преподаватель ещё не создал рубрику оценки.</p> : <>
            <select value={rubric} onChange={(e) => setRubric(e.target.value)}>{rubrics.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
            <button className="primary" onClick={run}>Запустить оценку</button></>}</div>
      )}
      {msg && <p className="muted" role="status">{msg}</p>}
      <Link to="/app">К списку пациентов</Link>
    </section>
  );
}
