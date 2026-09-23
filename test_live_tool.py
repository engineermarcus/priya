import asyncio
import os
from google import genai
from google.genai import types

client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# Native Python function signature for auto-schema generation
def bash(command: str) -> str:
    """Run a bash command directly on the system and return stdout/stderr."""
    pass

config = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    thinking_config=types.ThinkingConfig(thinking_level="medium"),
    system_instruction=types.Content(
        parts=[
            types.Part(
                text=(
                    "You are Priya, a system assistant. "
                    "CRITICAL: When the user asks you to run a command or check files, "
                    "DO NOT speak any introductory audio filler (do not say 'let me check' or 'running command'). "
                    "You MUST immediately issue a tool call to 'bash'. "
                    "Only speak AFTER receiving the tool response."
                )
            )
        ]
    ),
    tools=[bash],
)

async def test():
    async with client.aio.live.connect(
        model="gemini-3.8-live-extended-thinking", config=config
    ) as session:
        print("Connected. Sending request...")
        await session.send_client_content(
            turns=[
                {
                    "role": "user",
                    "parts": [{"text": "Run bash tool to list files in current directory with 'ls -la'."}],
                }
            ],
            turn_complete=True,
        )
        
        async for response in session.receive():
            print("\n--- RAW SERVER FRAME ---")
            print(f"server_content={response.server_content}")
            print(f"tool_call={response.tool_call}")
            
            if response.tool_call:
                print("\n[SUCCESS] Tool Call Received!")
                for fc in response.tool_call.function_calls:
                    print(f"Function: {fc.name}")
                    print(f"Args: {fc.args}")
                break

asyncio.run(test())
