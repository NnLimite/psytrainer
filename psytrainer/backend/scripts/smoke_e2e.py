"""Сквозная проверка по реальному HTTP: PostgreSQL + миграции + настоящий OllamaClient против имитации Ollama +
uvicorn. Проходит весь путь: кейс -> публикация -> сессия со стримингом -> оценка. Запуск: python scripts/smoke_e2e.py"""
import json
import os
import subprocess
import sys
import tempfile
import time

import httpx
import pgserver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API, OLLAMA = "http://127.0.0.1:18000", "http://127.0.0.1:18001"
FAKE_OLLAMA = '''
import json, re
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
app = FastAPI()
@app.get("/api/tags")
async def tags(): return {"models": []}
@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json(); system = body["messages"][0]["content"]
    if body.get("stream"):
        async def gen():
            for t in ["Ну", ", ", "типа", " нормально", "..."]:
                yield json.dumps({"message": {"content": t}, "done": False}) + "\\n"
            yield json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 50, "eval_count": 5}) + "\\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")
    if "супервизор" in system:
        codes = re.findall(r"^- (\\w+):", system, re.M)
        out = {"scores": [{"criterion_code": c, "score": 3, "evidence_turn_no": 1, "comment": "ок"} for c in codes], "summary": "Хорошо."}
    else:
        out = {"empathy": 0.8, "open_question": 0.6}
    return JSONResponse({"message": {"content": json.dumps(out)}, "prompt_eval_count": 10, "eval_count": 5})
'''


def wait(url: str, tries: int = 60) -> None:
    for _ in range(tries):
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.5)
    raise SystemExit(f"не поднялся {url}")


def main() -> int:
    pg = pgserver.get_server(tempfile.mkdtemp())
    db_url = f"postgresql+asyncpg://postgres@/postgres?host={pg.get_uri().split('host=')[1]}"
    storage = tempfile.mkdtemp()
    env = {**os.environ, "PSY_DATABASE_URL": db_url, "PSY_REDIS_URL": "", "PSY_OLLAMA_URL": OLLAMA, "PSY_COOKIE_SECURE": "false",
           "PSY_STORAGE_DIR": storage, "PSY_SECRET_KEY": "smoke-secret-smoke-secret-smoke-secret"}
    procs = []
    try:
        open(os.path.join(storage, "fake_ollama.py"), "w").write(FAKE_OLLAMA)
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "fake_ollama:app", "--port", "18001", "--log-level", "warning"],
                                      cwd=storage))
        for cmd in (["alembic", "upgrade", "head"],
                    ["app.cli", "create-user", "--email", "t@uni.ru", "--role", "teacher", "--org-name", "Вуз", "--password", "teacher-password-1"],
                    ["app.cli", "create-user", "--email", "s@uni.ru", "--role", "student", "--org-name", "Вуз", "--password", "student-password-1"]):
            args = [sys.executable, "-m", *cmd]
            subprocess.run(args, cwd=ROOT, env=env, check=True, capture_output=True)
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app_factory", "--factory", "--port", "18000",
                                       "--log-level", "warning"], cwd=ROOT, env=env))
        wait(f"{OLLAMA}/api/tags")
        wait(f"{API}/api/v1/health")
        v1 = f"{API}/api/v1"
        ready = httpx.get(f"{v1}/ready").json()
        assert ready["checks"] == {"database": "ok", "redis": "ok", "llm": "ok"}, ready

        def login(email, pw):
            r = httpx.post(f"{v1}/auth/login", json={"email": email, "password": pw})
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        t, s = login("t@uni.ru", "teacher-password-1"), login("s@uni.ru", "student-password-1")
        model = json.load(open(os.path.join(ROOT, "examples", "dima.json"), encoding="utf-8"))
        case = httpx.post(f"{v1}/cases", json={"title": model["title"], "visibility": "org", "case_model": model}, headers=t).json()
        pub = httpx.post(f"{v1}/cases/{case['id']}/versions/1/publish", headers=t)
        pub.raise_for_status()
        version_id = pub.json()["id"]
        httpx.put(f"{v1}/me/consents/transcript_processing", json={"granted": True}, headers=s).raise_for_status()
        sess = httpx.post(f"{v1}/sessions", json={"case_version_id": version_id}, headers=s).json()

        tokens, done = [], None
        with httpx.stream("POST", f"{v1}/sessions/{sess['id']}/messages/stream", json={"text": "Расскажите, что вас беспокоит?"},
                          headers=s, timeout=30) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    tokens.append(ev["text"]) if ev["type"] == "token" else None
                    done = ev if ev["type"] == "done" else done
        assert "".join(tokens) == "Ну, типа нормально..." and done["features_source"] == "llm", (tokens, done)
        assert done["state"]["openness"] > 0

        httpx.post(f"{v1}/sessions/{sess['id']}/end", headers=s).raise_for_status()
        rub = httpx.post(f"{v1}/rubrics", json={"name": "Навыки", "criteria": [{"code": "empathy", "title": "Эмпатия", "weight": 1}]}, headers=t).json()
        job = httpx.post(f"{v1}/sessions/{sess['id']}/assessments", json={"rubric_id": rub["id"]}, headers=s)
        assert job.status_code == 202, job.text
        for _ in range(40):
            j = httpx.get(f"{v1}/jobs/{job.json()['id']}", headers=s).json()
            if j["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.25)
        assert j["status"] == "succeeded", j
        a = httpx.get(f"{API}{j['result_url']}", headers=s).json()
        assert a["total_score"] == 75.0 and a["scores"][0]["evidence_turn_id"], a

        print("SMOKE OK: сессия со стримингом и оценка прошли по реальному HTTP; токенов в стриме:", len(tokens),
              "| балл:", a["total_score"], "| состояние:", {k: round(v, 2) for k, v in done["state"].items()})
        return 0
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
        pg.cleanup()


if __name__ == "__main__":
    sys.exit(main())
