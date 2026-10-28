"""MCP Browser Bridge: existing Firefox tabs through the addon native host."""

import argparse
import asyncio
import base64
import json
from pathlib import Path

from mcp_browser_bridge.regular import (
    checked_request,
    compatibility,
    create_regular_server,
    upload,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stdio")
    diagnostic = sub.add_parser("call")
    diagnostic.add_argument("operation", choices=["status", "tabs", "snapshot", "act", "upload", "downloads", "focus", "wait", "attention", "close", "open", "navigate", "screenshot"])
    diagnostic.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    if args.command == "stdio":
        create_regular_server().run()
    else:
        try:
            arguments = json.loads(args.arguments)
            # Screenshots are written to a file named by "out" instead of printed.
            out = arguments.pop("out", None) if args.operation == "screenshot" else None
            if args.operation == "screenshot" and not out:
                raise ValueError('Screenshot needs "out": a path for the JPEG file')
            result = asyncio.run(
                upload(**arguments) if args.operation == "upload"
                else compatibility(arguments.get("tab_id")) if args.operation == "status"
                else checked_request({**arguments, "op": args.operation})
            )
            if out:
                Path(out).write_bytes(base64.b64decode(result.pop("data")))
                result["saved_to"] = str(Path(out).resolve())
            print(json.dumps(result))
        except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
            parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
