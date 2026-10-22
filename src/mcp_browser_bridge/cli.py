"""MCP Browser Bridge: existing Firefox tabs through the addon native host."""

import argparse
import asyncio
import json

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
    diagnostic.add_argument("operation", choices=["status", "tabs", "snapshot", "act", "upload", "downloads", "focus", "wait"])
    diagnostic.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    if args.command == "stdio":
        create_regular_server().run()
    else:
        try:
            arguments = json.loads(args.arguments)
            result = asyncio.run(
                upload(**arguments) if args.operation == "upload"
                else compatibility(arguments.get("tab_id")) if args.operation == "status"
                else checked_request({**arguments, "op": args.operation})
            )
            print(json.dumps(result))
        except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
            parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
