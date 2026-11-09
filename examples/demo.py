"""Contoh pemakaian library.

    $env:DEEPSEEK_TOKEN="..."      # atau: --token-file token.json
    python examples/demo.py
    python examples/demo.py --stream --model expert
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepseek_web_api import DeepSeekWebClient  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default="Perkenalkan dirimu dalam dua kalimat.")
    parser.add_argument("--model", default="default", choices=["default", "expert", "vision"])
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--thinking", action="store_true", help="tampilkan reasoning")
    parser.add_argument("--token-file")
    args = parser.parse_args()

    client = DeepSeekWebClient(
        token=os.environ.get("DEEPSEEK_TOKEN"),
        token_file=args.token_file,
    )

    async with client:
        if args.stream:
            async for chunk in client.stream_text(
                args.prompt,
                model_type=args.model,
                thinking_enabled=args.model == "expert",
                include_thinking=args.thinking,
            ):
                print(chunk, end="", flush=True)
            print()
        else:
            print(
                await client.chat(
                    args.prompt,
                    model_type=args.model,
                    thinking_enabled=args.model == "expert",
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
