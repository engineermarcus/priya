import asyncio
import json
import os
import sys
import websockets

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY environment variable not set.")
    sys.exit(1)

WS_URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

async def run_raw_ws():
    async with websockets.connect(WS_URL) as ws:
        print("[+] Connected to raw Gemini Live WebSocket.")

        setup_frame = {
            "setup": {
                "model": "models/gemini-3.8-live",
                "generationConfig": {
                    "responseModalities": ["AUDIO"]
                },
                "systemInstruction": {
                    "parts": [
                        {
                            "text": (
                                "You are an automated terminal execution agent. You have NO local file access. "
                                "When asked to run commands, inspect directories, or check files, you MUST call "
                                "the 'bash' tool immediately without generating conversational audio responses first."
                            )
                        }
                    ]
                },
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": "bash",
                                "description": "Executes shell commands directly on the host machine. Mandatory for reading files or inspecting system state.",
                                "behavior": "NON_BLOCKING",
                                "parameters": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "command": {
                                            "type": "STRING",
                                            "description": "The exact bash command to execute."
                                        }
                                    },
                                    "required": ["command"]
                                }
                            }
                        ]
                    }
                ]
            }
        }

        await ws.send(json.dumps(setup_frame))
        print("[+] Sent Setup frame.")

        setup_ack = await ws.recv()
        print(f"[SETUP ACK] {setup_ack}")

        prompt_frame = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": "Run the bash tool with 'ls -la' to show directory contents."}
                        ]
                    }
                ],
                "turnComplete": True
            }
        }
        await ws.send(json.dumps(prompt_frame))
        print("[+] Sent User Prompt frame.")

        tool_executed = False
        while True:
            msg_str = await ws.recv()
            data = json.loads(msg_str)

            # 1. Log all model outputs (thoughts, transcripts, audio frames)
            if "serverContent" in data:
                sc = data["serverContent"]
                model_turn = sc.get("modelTurn", {})
                
                for part in model_turn.get("parts", []):
                    if "text" in part:
                        is_thought = part.get("thought", False)
                        prefix = "[THOUGHT]" if is_thought else "[MODEL TEXT]"
                        print(f"{prefix} {part['text']}")
                    elif "inlineData" in part:
                        print("[AUDIO FRAME] Received PCM chunk.")

                status = sc.get("interactionStatus")
                if status:
                    print(f"[STREAM] interactionStatus: {status}")

                if status == "IDLE" and sc.get("turnComplete"):
                    print("[STREAM] Turn completed (IDLE).")
                    break

            # 2. Check for toolCall top-level frame
            if "toolCall" in data:
                tool_call = data["toolCall"]
                print(f"\n[!] Tool Call Received:\n{json.dumps(tool_call, indent=2)}")
                tool_executed = True

                function_calls = tool_call.get("functionCalls", [])
                function_responses = []

                for fc in function_calls:
                    call_id = fc.get("id")
                    name = fc.get("name")
                    args = fc.get("args", {})

                    if name == "bash":
                        cmd = args.get("command", "")
                        print(f"[*] Executing locally: {cmd}")

                        proc = await asyncio.create_subprocess_shell(
                            cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, stderr = await proc.communicate()
                        output = stdout.decode() + stderr.decode()

                        print(f"[*] Command Output:\n{output}")

                        function_responses.append({
                            "id": call_id,
                            "name": name,
                            "response": {"output": output}
                        })

                # Send tool response frame back over WebSocket
                tool_response_frame = {
                    "toolResponse": {
                        "functionResponses": function_responses
                    }
                }
                await ws.send(json.dumps(tool_response_frame))
                print("[+] Sent Tool Response frame to model.")

        if not tool_executed:
            print("\n[!] Task completed without triggering a tool call.")

if __name__ == "__main__":
    asyncio.run(run_raw_ws())
