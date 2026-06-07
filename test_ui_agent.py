import asyncio
import os
from browser_use import Agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="google/gemini-2.5-flash",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)

async def main():
    agent = Agent(
        task="""Go to http://localhost:5173. You are an expert QA tester reviewing a chatbot companion app.

Login if prompted (user: mike, password: try common defaults or look for a register option).

Systematically test:
1. Companion selection screen - click each companion, verify cards load
2. Chat - send a message, verify response streams back, send several quickly
3. Companion switcher dropdown in chat header - switch companions
4. Settings page - click through Account, Companion, Session tabs
5. Companion tab - edit a field, hit Save, verify it persists after navigating away and back
6. Session tab - adjust relationship sliders, verify they move
7. Create a new chat, verify it appears in the session list
8. Switch between sessions

For each interaction report:
- What you clicked/typed
- What happened (expected vs actual)
- Any errors, broken layouts, missing data, or confusing UX
- Any elements that don't respond to clicks

Be thorough. Test edge cases like empty fields, rapid clicking, navigating back/forward.""",
        llm=llm,
    )
    result = await agent.run()
    print("\n\n=== FINAL REPORT ===")
    print(result)

asyncio.run(main())
