from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .agent import Assistant
from .config import load_config
from .memory import MemoryStore


async def main(message: str) -> None:
    config = load_config(Path("config.json"))
    store = MemoryStore(config.memory_dir)
    store.ensure()
    assistant = Assistant(config, store)
    texts: list[str] = []
    try:
        async for event in assistant.stream(message):
            if event["type"] == "text":
                texts.append(event["text"])
                print(event["text"])
            elif event["type"] == "tool":
                print(f"[{event['name']}] {event['target']}")
            elif event["type"] == "error":
                print(f"[エラー] {event['message']}", file=sys.stderr)
        proposals = await assistant.propose(message, "".join(texts))
        for p in proposals:
            print(f"[記憶の提案] {p.file}: {p.text}")
    finally:
        await assistant.reset()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('使い方: python -m assistant.cli "メッセージ"')
    asyncio.run(main(sys.argv[1]))
