#!/usr/bin/env python3
"""Seed script for local chatbot development. Populates sessions, memory entries, and persona configuration so the chatbot API can be tested immediately."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any, Sequence

import httpx


DEFAULT_BASE_URL = "http://localhost:8000"
WARM_MESSAGE = (
    "Seed local development context: use the default helpful chatbot persona, "
    "remember that this is a local API smoke-test session, and be ready for "
    "multi-turn development conversation checks."
)


@dataclass(slots=True)
class SeedSummary:
    session_ids_created: list[str]
    message_sent: str | None = None


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed local chatbot API data for development conversations.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SEED_BASE_URL", DEFAULT_BASE_URL),
        help=(
            "Base URL for the chatbot API. Defaults to SEED_BASE_URL or "
            f"{DEFAULT_BASE_URL}."
        ),
    )
    parser.add_argument(
        "--no-warm",
        action="store_true",
        help="Create a session without sending the initial warm-up chat message.",
    )
    return parser.parse_args(argv)


async def _create_session(client: httpx.AsyncClient) -> str:
    response = await client.post("/sessions", timeout=10)
    response.raise_for_status()

    payload = response.json()
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Session create response did not include a session_id.")

    return session_id


async def _send_warm_message(client: httpx.AsyncClient, session_id: str) -> None:
    response = await client.post(
        "/chat",
        json={"session_id": session_id, "message": WARM_MESSAGE},
        timeout=10,
    )
    response.raise_for_status()


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _print_http_error(exc: httpx.HTTPError) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        request = exc.request
        body = _response_body(response)
        print(
            (
                f"HTTP error: {request.method} {request.url} returned "
                f"{response.status_code}: {body}"
            ),
            file=sys.stderr,
        )
        return

    print(f"HTTP error: {exc}", file=sys.stderr)


def _response_body(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return response.text.strip() or "<empty response body>"

    return str(payload)


def _print_summary(summary: SeedSummary) -> None:
    print("Seed summary:")
    print(f"  session_ids_created: {summary.session_ids_created}")
    print(f"  message_sent: {summary.message_sent if summary.message_sent is not None else 'none'}")


async def main() -> None:
    args = _parse_args()
    base_url = _normalize_base_url(args.base_url)
    summary = SeedSummary(session_ids_created=[])

    try:
        async with httpx.AsyncClient(base_url=base_url, follow_redirects=True) as client:
            session_id = await _create_session(client)
            summary.session_ids_created.append(session_id)
            print(f"Created session ID: {session_id}")

            if not args.no_warm:
                await _send_warm_message(client, session_id)
                summary.message_sent = WARM_MESSAGE
                print("Sent initial seed message to /chat.")
    except httpx.HTTPError as exc:
        _print_http_error(exc)
        raise SystemExit(1) from exc
    except ValueError as exc:
        print(f"Seed error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    _print_summary(summary)


if __name__ == "__main__":
    asyncio.run(main())
