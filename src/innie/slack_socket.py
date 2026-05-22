from __future__ import annotations

from typing import Any, Callable


class SlackSocketModeEventSource:
    def __init__(
        self,
        app_token: str,
        *,
        client_factory: Callable[[str], Any] | None = None,
        response_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._app_token = app_token
        self._client_factory = client_factory or self._default_client_factory
        self._response_factory = response_factory or self._default_response_factory

    async def receive_once(self) -> dict:
        received: list[dict] = []

        async def _on_event(client, req) -> None:
            await client.send_socket_mode_response(self._response_factory(req.envelope_id))
            if req.type == "events_api":
                received.append(req.payload)

        client = self._client_factory(self._app_token)
        client.socket_mode_request_listeners.append(_on_event)
        try:
            await client.connect()
            while not received:
                # Real Slack SDK clients call listeners from their own receive loop.
                # Tests use connect() to synchronously feed one request.
                import asyncio

                await asyncio.sleep(0.05)
            return received[0]
        finally:
            await client.close()

    @staticmethod
    def _default_client_factory(app_token: str):
        from slack_sdk.socket_mode.aiohttp import SocketModeClient

        return SocketModeClient(app_token=app_token)

    @staticmethod
    def _default_response_factory(envelope_id: str):
        from slack_sdk.socket_mode.response import SocketModeResponse

        return SocketModeResponse(envelope_id=envelope_id)
