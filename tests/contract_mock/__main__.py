"""Serve the Contract v1 mock: python -m tests.contract_mock [--host H] [--port P]."""

from __future__ import annotations

import argparse

import uvicorn

from tests.contract_mock.app import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Contract v1 mock API (NOP-151)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
