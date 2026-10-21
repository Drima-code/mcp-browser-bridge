"""MCP Browser Bridge: existing Firefox tabs through the addon native host."""

import argparse
import asyncio
import json

from mcp_browser_bridge.regular import create_regular_server, request, upload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stdio")
    diagnostic = sub.add_parser("call")
    diagnostic.add_argument("operation", choices=["tabs", "snapshot", "act", "upload"])
    diagnostic.add_argument("arguments", nargs="?", default="{}")
    args = parser.parse_args()
    if args.command == "stdio":
        create_regular_server().run()
    else:
        try:
            arguments = json.loads(args.arguments)
            result = asyncio.run(
                upload(**arguments) if args.operation == "upload"
                else request({**arguments, "op": args.operation})
            )
            print(json.dumps(result))
        except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
            parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
