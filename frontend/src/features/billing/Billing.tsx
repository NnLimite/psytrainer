import { useEffect, useState } from "react";
import { api, errMsg, post } from "../../api/client";
import { useAuth } from "../auth/AuthContext";

type Sub = { plan: string; status: string; valid_until: string | null };
// Идентификаторы планов совпадают с PSY_PLANS на бэкенде; цены показывает платёжная страница провайдера.
const PLANS = [{ id: "student_monthly", label: "Месяц" }, { id: "student_yearly", label: "Год" }];

export default function Billing() {
  const { user } = useAuth();
  const [sub, setSub] = useState<Sub | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Sub>("/me/subscription").then(setSub).catch((e) => setErr(errMsg(e))); }, []);
  const buy = async (plan: string) => {
    try { const back = `${location.origin}/app/billing`; location.href = (await post<{ checkout_url: string }>("/billing/checkout", { plan, success_url: back, cancel_url: back })).checkout_url; }
    catch (e) { setErr(errMsg(e)); }
  };
  const plans = user?.role === "org_admin" ? [...PLANS, { id: "org_yearly", label: "Лицензия организации, год" }] : PLANS;
  return (
    <section className="narrow"><h1>Тариф</h1>
      {sub && <p className="panel">Сейчас: <b>{sub.plan}</b> ({sub.status}){sub.valid_until && <> до {sub.valid_until}</>}</p>}
      <div className="panel"><h3>Оформить подписку</h3><div className="row">{plans.map((p) => <button className="primary" key={p.id} onClick={() => buy(p.id)}>{p.label}</button>)}</div></div>
      {err && <p className="error" role="alert">{err}</p>}</section>
  );
}
