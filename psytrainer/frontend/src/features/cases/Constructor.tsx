import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, errMsg, getWithEtag, patch, post, putIfMatch } from "../../api/client";

type Disorder = { classifier: string; code: string; name: string };
type Trig = { id: string; keywords: string; situation: string; automatic_thought: string; emotion: string; behavior: string; intensity: number };
type Form = Record<string, any>;
const TEXT: [string, string][] = [["title", "Название кейса"], ["name", "Имя пациента"], ["gender", "Пол"], ["occupation", "Занятие"], ["tone", "Тон речи"]];
const LINES: [string, string][] = [["background", "История (по строке на пункт)"], ["core_beliefs", "Глубинные убеждения"], ["rules", "Правила «если…, то…»"],
  ["defenses", "Защиты"], ["quirks", "Особенности речи"], ["goals", "Цели"], ["strengths", "Сильные стороны"]];
const BARS = [["trust", "Доверие"], ["anxiety", "Тревога"], ["resistance", "Сопротивление"], ["openness", "Открытость"]];
const EMPTY: Form = { title: "", name: "", age: 25, gender: "", occupation: "", presenting_problem: "", tone: "", verbosity: "moderate", triggers: [] as Trig[],
  background: "", core_beliefs: "", rules: "", defenses: "", quirks: "", goals: "", strengths: "", disorders: [] as Disorder[],
  baseline: { trust: 0.25, anxiety: 0.5, resistance: 0.5, openness: 0.25 }, secret: "", trustT: 0.65, openT: 0.5 };
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const toModel = (f: Form) => ({
  title: f.title, name: f.name, age: Number(f.age), gender: f.gender, occupation: f.occupation, language: "ru", presenting_problem: f.presenting_problem,
  background: lines(f.background), core_beliefs: lines(f.core_beliefs), rules: lines(f.rules), defenses: lines(f.defenses), goals: lines(f.goals), strengths: lines(f.strengths),
  triggers: (f.triggers as Trig[]).map((t) => ({ ...t, keywords: t.keywords.split(",").map((k) => k.trim()).filter(Boolean) })),
  communication_style: { verbosity: f.verbosity, tone: f.tone, quirks: lines(f.quirks) },
  disorders: (f.disorders as Disorder[]).map((d) => ({ ...d, role: "primary" })), baseline: f.baseline,
  hidden_disclosure: f.secret.trim() ? { text: f.secret, trust_threshold: f.trustT, openness_threshold: f.openT } : null,
});
const fromModel = (m: any, title: string): Form => ({
  ...EMPTY, ...m, title, tone: m.communication_style?.tone ?? "", verbosity: m.communication_style?.verbosity ?? "moderate",
  quirks: (m.communication_style?.quirks ?? []).join("\n"), triggers: (m.triggers ?? []).map((t: any) => ({ ...t, keywords: t.keywords.join(", ") })),
  ...Object.fromEntries(["background", "core_beliefs", "rules", "defenses", "goals", "strengths"].map((k) => [k, (m[k] ?? []).join("\n")])),
  secret: m.hidden_disclosure?.text ?? "", trustT: m.hidden_disclosure?.trust_threshold ?? 0.65, openT: m.hidden_disclosure?.openness_threshold ?? 0.5,
});

export default function Constructor() {
  const { id } = useParams();
  const nav = useNavigate();
  const [f, setF] = useState<Form>(EMPTY);
  const [ver, setVer] = useState<{ no: number; etag: string | null; status: string } | null>(null);
  const [all, setAll] = useState<Disorder[]>([]);
  const [report, setReport] = useState<{ errors: { path: string; message: string }[]; warnings: { path: string; message: string }[] } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setF((p) => ({ ...p, [k]: v }));

  useEffect(() => {
    api<{ items: Disorder[] }>("/disorders?limit=100").then((p) => setAll(p.items)).catch(() => {});
    if (!id) return;
    (async () => {
      const c = await api<{ title: string; versions: { version_no: number; status: string }[] }>(`/cases/${id}`);
      const v = [...c.versions].reverse().find((x) => x.status === "draft") ?? c.versions[c.versions.length - 1];
      const { data, etag } = await getWithEtag<{ case_model: any }>(`/cases/${id}/versions/${v.version_no}`);
      setF(fromModel(data.case_model, c.title)); setVer({ no: v.version_no, etag, status: v.status });
    })().catch((e) => setMsg(errMsg(e)));
  }, [id]);

  const guard = (fn: () => Promise<void>) => async () => { try { setMsg(null); await fn(); } catch (e) { setMsg(errMsg(e)); } };
  const save = guard(async () => {
    if (!id) { const c = await post<{ id: string }>("/cases", { title: f.title, visibility: "org", case_model: toModel(f) }); nav(`/app/cases/${c.id}`, { replace: true }); return; }
    await patch(`/cases/${id}`, { title: f.title });
    const r = await putIfMatch(`/cases/${id}/versions/${ver!.no}`, toModel(f), ver!.etag!);
    setVer({ ...ver!, etag: r.etag }); setMsg("Сохранено");
  });
  const validate = guard(async () => { await save(); setReport(await post(`/cases/${id}/versions/${ver!.no}/validate`)); });
  const publish = guard(async () => { await save(); await post(`/cases/${id}/versions/${ver!.no}/publish`); setVer({ ...ver!, status: "published" }); setMsg("Опубликовано: версия теперь неизменяема"); });
  const newDraft = guard(async () => { await post(`/cases/${id}/versions`, {}); window.location.reload(); });
  const readonly = ver?.status === "published";
  const trig = (i: number, k: string, v: unknown) => set("triggers", f.triggers.map((t: Trig, j: number) => (j === i ? { ...t, [k]: v } : t)));

  return (
    <section className="narrow">
      <h1>{id ? "Редактирование пациента" : "Новый пациент"}</h1>
      {readonly && <p className="notice">Версия опубликована и неизменяема. <button className="ghost" onClick={newDraft}>Создать черновик новой версии</button></p>}
      <fieldset disabled={readonly} className="panel form">
        {TEXT.map(([k, l]) => <label key={k}>{l}<input value={f[k]} onChange={(e) => set(k, e.target.value)} /></label>)}
        <label>Возраст<input type="number" min={1} value={f.age} onChange={(e) => set("age", e.target.value)} /></label>
        <label>С чем пришёл<textarea rows={3} value={f.presenting_problem} onChange={(e) => set("presenting_problem", e.target.value)} /></label>
        {LINES.map(([k, l]) => <label key={k}>{l}<textarea rows={3} value={f[k]} onChange={(e) => set(k, e.target.value)} /></label>)}
        <label>Расстройство (МКБ-11 / DSM-5)<select value="" onChange={(e) => { const d = all.find((x) => `${x.classifier}${x.code}` === e.target.value); if (d) set("disorders", [...f.disorders, d]); }}>
          <option value="">Добавить…</option>{all.map((d) => <option key={d.classifier + d.code} value={d.classifier + d.code}>{d.classifier} {d.code} {d.name}</option>)}</select></label>
        <p>{(f.disorders as Disorder[]).map((d, i) => <button key={i} className="ghost" onClick={() => set("disorders", f.disorders.filter((_: unknown, j: number) => j !== i))}>{d.code} ✕</button>)}</p>
        <h3>Исходное состояние</h3>
        {BARS.map(([k, l]) => <label key={k}>{l}: {f.baseline[k]}<input type="range" min={0} max={1} step={0.05} value={f.baseline[k]} onChange={(e) => set("baseline", { ...f.baseline, [k]: Number(e.target.value) })} /></label>)}
        <h3>Триггеры</h3>
        {(f.triggers as Trig[]).map((t, i) => (
          <div className="panel" key={i}>
            {([["id", "Идентификатор (латиницей)"], ["keywords", "Корни слов через запятую (например: отц, папа)"], ["situation", "Ситуация"], ["automatic_thought", "Автоматическая мысль"], ["emotion", "Эмоция"], ["behavior", "Поведение"]] as const)
              .map(([k, l]) => <label key={k}>{l}<input value={t[k]} onChange={(e) => trig(i, k, e.target.value)} /></label>)}
            <label>Сила {t.intensity}<input type="range" min={0} max={1} step={0.1} value={t.intensity} onChange={(e) => trig(i, "intensity", Number(e.target.value))} /></label>
            <button className="ghost" onClick={() => set("triggers", f.triggers.filter((_: Trig, j: number) => j !== i))}>Удалить триггер</button></div>
        ))}
        <button className="ghost" onClick={() => set("triggers", [...f.triggers, { id: `t${f.triggers.length + 1}`, keywords: "", situation: "", automatic_thought: "", emotion: "", behavior: "", intensity: 0.5 }])}>+ Триггер</button>
        <label>Тайна (раскроется при достаточном доверии; можно оставить пустой)<textarea rows={2} value={f.secret} onChange={(e) => set("secret", e.target.value)} /></label>
      </fieldset>
      {msg && <p className="notice" role="status">{msg}</p>}
      {report && <div className="panel">{report.errors.length === 0 && <p>Ошибок нет.</p>}
        {report.errors.map((x, i) => <p className="error" key={i}>{x.path}: {x.message}</p>)}{report.warnings.map((x, i) => <p className="muted" key={i}>⚠ {x.path}: {x.message}</p>)}</div>}
      {!readonly && <div className="row"><button className="primary" onClick={save}>Сохранить</button>
        {id && <><button onClick={validate}>Проверить</button><button onClick={publish}>Опубликовать</button></>}</div>}
    </section>
  );
}
