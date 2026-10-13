from __future__ import annotations

from pathlib import Path

import uvicorn

from .agent import Assistant
from .config import load_config
from .memory import MemoryStore
from .server import create_app


def main() -> None:
    config = load_config(Path("config.json"))
    store = MemoryStore(config.memory_dir)
    created = store.ensure()
    if created:
        print(f"記憶ファイルを新規作成しました: {', '.join(created)}")
    assistant = Assistant(config, store)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    allowed_hosts = {f"127.0.0.1:{config.port}", f"localhost:{config.port}"}
    app = create_app(assistant, store, static_dir, allowed_hosts=allowed_hosts)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
