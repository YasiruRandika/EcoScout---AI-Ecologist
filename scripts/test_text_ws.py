"""Test text message via WebSocket."""
import asyncio
import json
import websockets


async def test():
    uri = "ws://localhost:8000/ws/test-user/test-text-msg"

    print("=== Test: Send text message ===")
    async with websockets.connect(uri, open_timeout=15) as ws:
        msg = json.dumps({"type": "text", "text": "Hello, what species can you see?"})
        await ws.send(msg)
        print("Sent text message")

        responses = []
        try:
            while True:
                data = await asyncio.wait_for(ws.recv(), timeout=15)
                parsed = json.loads(data)
                event_type = "unknown"
                if parsed.get("turnComplete"):
                    event_type = "turnComplete"
                elif parsed.get("content", {}).get("parts"):
                    parts = parsed["content"]["parts"]
                    texts = [p.get("text", "") for p in parts if p.get("text")]
                    audio = [p for p in parts if p.get("inlineData")]
                    if texts:
                        event_type = f"text: {texts[0][:80]}..."
                    elif audio:
                        event_type = f"audio chunk ({len(audio)} parts)"
                elif parsed.get("outputTranscription"):
                    t = parsed["outputTranscription"].get("text", "")
                    event_type = f"outputTranscription: {t[:80]}..."
                elif parsed.get("inputTranscription"):
                    t = parsed["inputTranscription"].get("text", "")
                    event_type = f"inputTranscription: {t[:80]}..."
                else:
                    event_type = str(list(parsed.keys()))[:80]
                responses.append(event_type)
                print(f"  Received: {event_type}")
        except asyncio.TimeoutError:
            pass

        if len(responses) > 0:
            print(f"PASS: Got {len(responses)} response events from agent")
        else:
            print("FAIL: No response from agent")


asyncio.run(test())
