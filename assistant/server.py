from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .memory import MemoryPathError, MemoryStore, is_valid_memory_path


class ChatRequest(BaseModel):
    message: str


class ProposalIn(BaseModel):
    file: str
    text: str
    summary: str | None = None


class ApplyRequest(BaseModel):
    items: list[ProposalIn]


class UndoRequest(BaseModel):
    file: str
    id: str


def _line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def create_app(
    assistant,
    store: MemoryStore,
    static_dir: Path,
    allowed_hosts: set[str] | None = None,
) -> FastAPI:
    app = FastAPI()

    if allowed_hosts is not None:
        allowed_origins = {f"http://{h}" for h in allowed_hosts}

        @app.middleware("http")
        async def guard_host_and_origin(request: Request, call_next):
            # DNS rebinding 対策: Host が許可リストにないリクエストを拒否する
            if request.headers.get("host") not in allowed_hosts:
                return JSONResponse({"detail": "許可されていない Host です"}, status_code=403)
            # CSRF 対策: 別オリジンのページからの POST を拒否する
            origin = request.headers.get("origin")
            if request.method == "POST" and origin is not None and origin not in allowed_origins:
                return JSONResponse({"detail": "許可されていない Origin です"}, status_code=403)
            return await call_next(request)

    @app.get("/")
    async def index():
        return FileResponse(Path(static_dir) / "index.html")

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        async def gen():
            texts: list[str] = []
            try:
                async for event in assistant.stream(req.message):
                    if event["type"] == "text":
                        texts.append(event["text"])
                    yield _line(event)
                full_text = "".join(texts)
                if full_text:
                    proposals = await assistant.propose(req.message, full_text)
                    yield _line(
                        {"type": "proposals", "items": [p.to_dict() for p in proposals]}
                    )
            except Exception as e:  # 会話を壊さず、画面にエラーを返す
                yield _line({"type": "error", "message": f"{type(e).__name__}: {e}"})
            yield _line({"type": "done"})

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/memory/apply")
    async def apply(req: ApplyRequest):
        # 1. 全件を先に検証し、一つでも不正なら何も書かない
        for item in req.items:
            if not is_valid_memory_path(item.file):
                raise HTTPException(
                    status_code=400, detail=f"書き込めない記憶ファイルです: {item.file}"
                )
            if not item.text.strip():
                raise HTTPException(status_code=400, detail="空の記憶は追加できません")

        # 2. 書き込み途中で失敗したら、書いた分をロールバックしてから返す
        applied: list[dict] = []
        try:
            for item in req.items:
                entry_id = store.append(item.file, item.text, item.summary)
                applied.append({"file": item.file, "text": item.text, "id": entry_id})
        except Exception as e:
            for done in reversed(applied):
                store.undo(done["file"], done["id"])
            if isinstance(e, (MemoryPathError, ValueError)):
                raise HTTPException(status_code=400, detail=str(e))
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
        return {"applied": applied}

    @app.post("/api/memory/undo")
    async def undo(req: UndoRequest):
        try:
            ok = store.undo(req.file, req.id)
        except MemoryPathError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": ok}

    @app.post("/api/interrupt")
    async def interrupt():
        await assistant.interrupt()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset():
        await assistant.reset()
        return {"ok": True}

    return app
