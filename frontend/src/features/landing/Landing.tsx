import { useEffect, useRef, useState } from "react";
import "./Landing.css";

type Bars = { trust: number; anxiety: number; resistance: number; openness: number };
type Msg = { from: "student" | "patient"; text: string };

const START: Bars = { trust: 0.25, anxiety: 0.55, resistance: 0.55, openness: 0.25 };
const LABELS: [keyof Bars, string][] = [
  ["trust", "Доверие"], ["anxiety", "Тревога"], ["resistance", "Сопротивление"], ["openness", "Открытость"],
];
const cl = (x: number) => Math.max(0, Math.min(1, x));

// Демо-сценарий скриптован и работает без бэкенда; реальные шкалы считает backend/app/engine.
const CHOICES = [
  { label: "Понимаю, звучит тяжело. Что для вас сейчас самое трудное?",
    reply: "Ну… на работе всё жду, что найдут ошибку. Наверное, глупо звучит.",
    d: { trust: 0.14, anxiety: -0.06, resistance: -0.08, openness: 0.12 },
    tip: "Эмпатия и открытый вопрос: доверие растёт, пациент делится личным." },
  { label: "Вам просто нужно меньше переживать.",
    reply: "Спасибо, капитан очевидность. Я до этого сам не додумался.",
    d: { trust: -0.08, anxiety: 0.04, resistance: 0.12, openness: -0.08 },
    tip: "Преждевременный совет обесценивает чувства: растёт сопротивление." },
  { label: "Вы сами себя накручиваете. Это ваша вина.",
    reply: "Знаете… я, пожалуй, пойду. Зря я это затеял.",
    d: { trust: -0.18, anxiety: 0.1, resistance: 0.16, openness: -0.14 },
    tip: "Осуждение рушит контакт: доверие падает быстрее, чем растёт." },
];

// TODO: цены и лимиты — заглушки, замените на свои (план в бэкенде: student_monthly / student_yearly).
const PLANS = [
  { name: "Старт", m: "0 ₽", y: "0 ₽", note: "Чтобы попробовать", hot: false, cta: "Начать бесплатно",
    items: ["1 пациент из каталога", "5 сессий в месяц", "Подсказки в тренировочном режиме"] },
  { name: "Практика", m: "299 ₽", y: "2 490 ₽", note: "Для регулярных тренировок", hot: true, cta: "Выбрать «Практику»",
    items: ["Все пациенты каталога", "Сессии без ограничений", "Разбор с привязкой к репликам", "Свой пациент в конструкторе"] },
];

const FAQ = [
  ["Это заменяет терапию или супервизию?", "Нет. Это тренажёр: пациент вымышленный, а симуляция не заменяет живую практику, супервизию и психологическую помощь."],
  ["Как ИИ-пациент решает, что ответить?", "Состояние пациента (доверие, тревогу, сопротивление) считают правила, а не модель. Модель лишь играет роль по карточке случая, поэтому пациент не «выздоравливает» за две реплики."],
  ["Что с моими диалогами?", "Транскрипты — ваши персональные данные. Вы даёте согласие на их обработку, можете выгрузить их и удалить аккаунт вместе с диалогами."],
  ["Нужно ли что-то устанавливать?", "Нет, всё работает в браузере."],
];

function Demo() {
  const [bars, setBars] = useState<Bars>(START);
  const [log, setLog] = useState<Msg[]>([{ from: "patient", text: "Здравствуйте. Девушка сказала, что надо прийти. Я, в целом, в порядке." }]);
  const [busy, setBusy] = useState(false);
  const [tip, setTip] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const timer = useRef<number>();
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [log, busy]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const pick = (c: (typeof CHOICES)[number]) => {
    if (busy) return;
    setLog((l) => [...l, { from: "student", text: c.label }]);
    setBusy(true);
    setTip(null);
    timer.current = window.setTimeout(() => {
      setLog((l) => [...l, { from: "patient", text: c.reply }]);
      setBars((b) => ({ trust: cl(b.trust + c.d.trust), anxiety: cl(b.anxiety + c.d.anxiety),
        resistance: cl(b.resistance + c.d.resistance), openness: cl(b.openness + c.d.openness) }));
      setTip(c.tip);
      setBusy(false);
    }, 1100);
  };
  const reset = () => { window.clearTimeout(timer.current); setBars(START); setBusy(false); setTip(null);
    setLog([{ from: "patient", text: "Здравствуйте. Девушка сказала, что надо прийти. Я, в целом, в порядке." }]); };

  return (
    <div className="demo" aria-label="Интерактивная демонстрация">
      <div className="demo-head"><span className="avatar">Д</span><div><b>Дмитрий, 27</b><small>тревога и перфекционизм</small></div>
        <button className="ghost" onClick={reset}>Сначала</button></div>
      <div className="bars">
        {LABELS.map(([k, label]) => (
          <div className="bar" key={k}><span>{label}</span>
            <div className="track"><i data-k={k} style={{ width: `${bars[k] * 100}%` }} /></div><b>{Math.round(bars[k] * 100)}</b></div>
        ))}
      </div>
      <div className="chat" ref={box} aria-live="polite">
        {log.map((m, i) => <p key={i} className={`msg ${m.from}`}>{m.text}</p>)}
        {busy && <p className="msg patient typing"><i /><i /><i /></p>}
      </div>
      {tip && <div className="tip">💡 {tip}</div>}
      <div className="choices"><small>Что скажете вы?</small>
        {CHOICES.map((c) => <button key={c.label} disabled={busy} onClick={() => pick(c)}>{c.label}</button>)}</div>
    </div>
  );
}

export default function Landing() {
  const [yearly, setYearly] = useState(false);
  return (
    <div className="lp">
      <header className="nav"><a className="logo" href="/">Psy<b>Trainer</b></a>
        <nav><a href="#how">Как это работает</a><a href="#pricing">Тарифы</a><a href="#faq">Вопросы</a></nav>
        <div className="nav-cta"><a className="link" href="/login">Войти</a><a className="btn sm" href="/register">Начать</a></div></header>

      <section className="hero">
        <div className="hero-text">
          <span className="pill">Тренажёр для студентов-психологов</span>
          <h1>Отрабатывайте консультирование на пациенте, который <em>реагирует на ваши слова</em></h1>
          <p>Доверие, тревога и сопротивление меняются по ходу беседы. После сессии вы получаете разбор по критериям, где каждый балл привязан к вашей реплике.</p>
          <div className="hero-cta"><a className="btn" href="/register">Попробовать бесплатно</a><a className="btn ghost" href="#how">Как это работает</a></div>
          <ul className="trust"><li>Без установки</li><li>Вымышленные пациенты</li><li>Свои данные можно удалить</li></ul>
        </div>
        <Demo />
      </section>

      <section id="how" className="sec"><h2>Три шага до первой практики</h2>
        <div className="grid3">
          {[["1", "Выберите пациента", "Каталог кейсов с разными расстройствами и стилями общения. Или соберите своего в конструкторе."],
            ["2", "Проведите беседу", "Пациент отвечает по-человечески: шутит, уходит от темы, открывается, когда вы заслужили доверие."],
            ["3", "Получите разбор", "Баллы по рубрике навыков с примерами ваших реплик и сравнение вашей модели случая с эталоном."]]
            .map(([n, t, d]) => <article className="card" key={n}><span className="num">{n}</span><h3>{t}</h3><p>{d}</p></article>)}
        </div></section>

      <section className="sec alt"><h2>Что внутри</h2>
        <div className="grid3">
          {[["🧠", "Пациент с логикой", "История, убеждения и защиты вместо списка симптомов: реакции объяснимы и повторяемы."],
            ["📈", "Видно, что сработало", "Шкалы пациента на экране в тренировочном режиме и «слепой» экзамен без подсказок."],
            ["🔁", "Продолжайте сессии", "Состояние пациента переносится между встречами, как в реальной работе."]]
            .map(([i, t, d]) => <article className="card" key={t}><span className="ico">{i}</span><h3>{t}</h3><p>{d}</p></article>)}
        </div></section>

      <section id="pricing" className="sec"><h2>Тарифы</h2>
        <div className="switch" role="group" aria-label="Период оплаты">
          <button className={!yearly ? "on" : ""} onClick={() => setYearly(false)}>Помесячно</button>
          <button className={yearly ? "on" : ""} onClick={() => setYearly(true)}>За год</button></div>
        <div className="grid2">
          {PLANS.map((p) => (
            <article className={`card plan ${p.hot ? "hot" : ""}`} key={p.name}>
              {p.hot && <span className="badge">Популярный</span>}
              <h3>{p.name}</h3><small>{p.note}</small>
              <div className="price">{yearly ? p.y : p.m}<span>{p.m === "0 ₽" ? "" : yearly ? " / год" : " / мес"}</span></div>
              <ul>{p.items.map((x) => <li key={x}>{x}</li>)}</ul>
              <a className={`btn ${p.hot ? "" : "ghost"}`} href="/register">{p.cta}</a></article>
          ))}
        </div></section>

      <section id="faq" className="sec alt narrow"><h2>Частые вопросы</h2>
        {FAQ.map(([q, a]) => <details key={q}><summary>{q}</summary><p>{a}</p></details>)}</section>

      <section className="final"><h2>Начните с первого разговора</h2>
        <p>Регистрация занимает минуту. Первую сессию можно провести бесплатно.</p>
        <a className="btn" href="/register">Создать аккаунт</a></section>

      <footer className="foot"><span>© PsyTrainer</span>
        <small>Тренажёр не оказывает психологическую помощь и не заменяет терапию, супервизию и медицинские услуги. Если вам тяжело, обратитесь к специалисту или в экстренную службу вашего региона.</small></footer>
    </div>
  );
}
