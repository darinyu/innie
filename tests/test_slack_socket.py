from __future__ import annotations

import asyncio
import unittest

from innie.slack_socket import SlackSocketModeEventSource


class FakeRequest:
    type = "events_api"
    envelope_id = "Env1"
    payload = {"event_id": "Ev1", "event": {"type": "message"}}


class FakeInteractiveRequest:
    type = "interactive"
    envelope_id = "EnvInteractive"
    payload = {
        "type": "block_actions",
        "actions": [{"action_id": "innie_show_progress_details", "value": "task_1"}],
    }


class FakeSocketClient:
    request_class = FakeRequest

    def __init__(self) -> None:
        self.socket_mode_request_listeners = []
        self.responses = []
        self.closed = False

    async def connect(self) -> None:
        await self.socket_mode_request_listeners[0](self, self.request_class())

    async def send_socket_mode_response(self, response) -> None:
        self.responses.append(response)

    async def close(self) -> None:
        self.closed = True


class FailingConnectSocketClient(FakeSocketClient):
    async def connect(self) -> None:
        raise RuntimeError("connect interrupted")


class InteractiveSocketClient(FakeSocketClient):
    request_class = FakeInteractiveRequest


class SlackSocketModeEventSourceTest(unittest.TestCase):
    def test_receive_once_acks_envelope_and_returns_payload(self) -> None:
        fake_client = FakeSocketClient()
        source = SlackSocketModeEventSource(
            "xapp-test",
            client_factory=lambda _token: fake_client,
            response_factory=lambda envelope_id: {"envelope_id": envelope_id},
        )

        payload = asyncio.run(source.receive_once())

        self.assertEqual("Ev1", payload["event_id"])
        self.assertEqual([{"envelope_id": "Env1"}], fake_client.responses)
        self.assertTrue(fake_client.closed)

    def test_receive_once_acks_and_returns_interactive_payload(self) -> None:
        fake_client = InteractiveSocketClient()
        source = SlackSocketModeEventSource(
            "xapp-test",
            client_factory=lambda _token: fake_client,
            response_factory=lambda envelope_id: {"envelope_id": envelope_id},
        )

        payload = asyncio.run(source.receive_once())

        self.assertEqual("block_actions", payload["type"])
        self.assertEqual("innie_show_progress_details", payload["actions"][0]["action_id"])
        self.assertEqual([{"envelope_id": "EnvInteractive"}], fake_client.responses)
        self.assertTrue(fake_client.closed)

    def test_receive_once_closes_client_when_connect_is_interrupted(self) -> None:
        fake_client = FailingConnectSocketClient()
        source = SlackSocketModeEventSource(
            "xapp-test",
            client_factory=lambda _token: fake_client,
            response_factory=lambda envelope_id: {"envelope_id": envelope_id},
        )

        with self.assertRaisesRegex(RuntimeError, "connect interrupted"):
            asyncio.run(source.receive_once())

        self.assertTrue(fake_client.closed)


if __name__ == "__main__":
    unittest.main()
