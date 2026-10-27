import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, post, refresh, setToken } from "../../api/client";

export type User = { id: string; email: string; full_name: string; role: "student" | "teacher" | "org_admin" | "platform_admin"; org_id: string | null };
type Auth = { user: User | null; ready: boolean; login: (e: string, p: string) => Promise<void>;
  register: (e: string, p: string, name: string, invitation?: string) => Promise<void>; logout: () => Promise<void> };
const Ctx = createContext<Auth>(null as unknown as Auth);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const load = async () => setUser(await api<User>("/me").catch(() => null));
  useEffect(() => { (async () => { if (await refresh()) await load(); setReady(true); })(); }, []);
  const enter = async (path: string, body: object) => { setToken((await post<{ access_token: string }>(path, body)).access_token); await load(); };
  return (
    <Ctx.Provider value={{ user, ready,
      login: (email, password) => enter("/auth/login", { email, password }),
      register: (email, password, full_name, invitation_token) => enter("/auth/register", { email, password, full_name, invitation_token }),
      logout: async () => { await post("/auth/logout").catch(() => {}); setToken(null); setUser(null); } }}>{children}</Ctx.Provider>
  );
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const loc = useLocation();
  if (!ready) return <p className="center">Загрузка…</p>;
  return user ? <>{children}</> : <Navigate to="/login" state={{ from: loc.pathname }} replace />;
}
