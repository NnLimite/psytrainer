import { BrowserRouter, Link, Navigate, Outlet, Route, Routes } from "react-router-dom";
import "./app.css";
import { AuthProvider, RequireAuth, useAuth } from "../features/auth/AuthContext";
import AuthPage from "../features/auth/AuthPage";
import Billing from "../features/billing/Billing";
import Catalog from "../features/cases/Catalog";
import Constructor from "../features/cases/Constructor";
import Debrief from "../features/debrief/Debrief";
import Teacher from "../features/teacher/Teacher";
import Landing from "../features/landing/Landing";
import SessionPage from "../features/session/SessionPage";
import Settings from "../features/settings/Settings";

function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <header className="top"><Link to="/app" className="brand">Psy<b>Trainer</b></Link>
        <nav><Link to="/app">Пациенты</Link>{user?.role !== "student" && <><Link to="/app/cases/new">Новый пациент</Link><Link to="/app/teacher">Кабинет</Link></>}<Link to="/app/billing">Тариф</Link><Link to="/app/settings">Настройки</Link></nav>
        <span className="muted">{user?.full_name}</span><button className="ghost" onClick={logout}>Выйти</button></header>
      <main className="page"><Outlet /></main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter><AuthProvider><Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/app" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Catalog />} /><Route path="session/:id" element={<SessionPage />} /><Route path="session/:id/debrief" element={<Debrief />} /><Route path="cases/new" element={<Constructor />} /><Route path="cases/:id" element={<Constructor />} />
        <Route path="teacher" element={<Teacher />} /><Route path="billing" element={<Billing />} /><Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes></AuthProvider></BrowserRouter>
  );
}
