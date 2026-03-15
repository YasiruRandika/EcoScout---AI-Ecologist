"""Quick WebSocket test for video generation flow."""
import asyncio
import json
import sys
import websockets


async def test_video_generation():
    uri = "ws://localhost:8080/ws/test-user/test-video-session"
    print("Connecting to WebSocket...")
    try:
        async with websockets.connect(uri, ping_interval=20) as ws:
            print("Connected! Sending video request...")
            msg = json.dumps({
                "type": "text",
                "text": "Generate a short nature video of a mushroom growing on a forest floor",
            })
            await ws.send(msg)
            print("Message sent. Waiting for responses...\n")

            for i in range(90):
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    try:
                        data = json.loads(resp)
                    except json.JSONDecodeError:
                        if len(resp) < 300:
                            print(f"  [binary/non-json] {len(resp)} bytes")
                        continue

                    msg_type = data.get("type", "")

                    if msg_type in ("video_started", "video_ready", "video_failed"):
                        print(f"\n*** {msg_type} ***")
                        print(json.dumps(data, indent=2))
                        if msg_type in ("video_ready", "video_failed"):
                            print("\nDone!")
                            return

                    parts = []
                    try:
                        for p in data.get("content", {}).get("parts", []):
                            if "text" in p:
                                parts.append(p["text"])
                            if "functionCall" in p:
                                fc = p["functionCall"]
                                name = fc.get("name", "")
                                args_str = json.dumps(fc.get("args", {}))
                                parts.append(f"TOOL_CALL: {name}({args_str[:200]})")
                            if "functionResponse" in p:
                                fr = p["functionResponse"]
                                resp_str = json.dumps(fr.get("response", {}))
                                parts.append(f"TOOL_RESP: {resp_str[:300]}")
                    except Exception:
                        pass

                    if parts:
                        combined = " | ".join(parts)
                        print(f"  Agent: {combined[:600]}")

                except asyncio.TimeoutError:
                    if i % 15 == 0:
                        elapsed = i * 2
                        print(f"  ... waiting ({elapsed}s elapsed)")

            print("\nTimeout: no video_ready/video_failed after 3 minutes")

    except ConnectionRefusedError:
        print("ERROR: Cannot connect to ws://localhost:8080 - is the server running?")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_video_generation())
