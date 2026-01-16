import imaplib
import chainlit as cl
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from agent_prompts import GMAIL_AGENT_PROMPT
from tools.gmail_tools import fetch_recent_emails, delete_emails_by_ids, set_imap_client, search_emails

@cl.on_chat_start
async def start():
    """
    Initializes the session by asking for credentials and setting up the agent.
    """
    
    # 1. Welcome Message
    await cl.Message(content="📬 **Inbox Zero, Zero Effort.**\n\nI'm your Agentic AI Gmail Cleaner. Tell me what to purge, and I'll autonomously make your inbox sparkle. ✨\n\nFirst, let's get connected...").send()

    res_key = await cl.AskUserMessage(content="🔑 Please enter your **OpenAI API Key**:\n(Get yours at [OpenAI Keys](https://platform.openai.com/api-keys))", timeout=600).send()
    if not res_key:
        await cl.Message(content="❌ Operation timed out. Please refresh to try again.").send()
        return
    openai_api_key = res_key["output"].strip()

    # 3. Ask for Gmail Address
    res_email = await cl.AskUserMessage(content="📧 Please enter your **Gmail Address** for which you want the cleanup:", timeout=600).send()
    if not res_email:
        await cl.Message(content="❌ Operation timed out. Please refresh to try again.").send()
        return
    email_account = res_email["output"].strip()

    # 4. Ask for App Password
    res_pass = await cl.AskUserMessage(content="🔒 Please enter your **Gmail App Password**:\n(This is NOT your login password. Check the Readme file for steps to generate one.)", timeout=600).send()
    if not res_pass:
        await cl.Message(content="❌ Operation timed out. Please refresh to try again.").send()
        return
    app_password = res_pass["output"].strip()

    # 5. Connect to Gmail
    msg = cl.Message(content=f"🔄 Connecting to {email_account}...")
    await msg.send()
    
    mail = None
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_account, app_password)
        
        # Inject the connection into our tools
        set_imap_client(mail)
        
        # Store mail object in session to close later if needed (optional)
        cl.user_session.set("mail_client", mail)
        
        msg.content = f"✅ **Connected Successfully to the Gmail Server!**"
        await msg.update()
        
    except Exception as e:
        msg.content = f"❌ **Connection Failed:** {e}\n\nPlease check your App Password and try again."
        await msg.update()
        return

    # 6. Initialize the Agent
    try:
        llm = ChatOpenAI(
            api_key=openai_api_key,
            model="gpt-4o-mini",
            temperature=0
        )

        tools = [fetch_recent_emails, delete_emails_by_ids, search_emails]

        agent_executor = create_agent(
            model=llm,
            tools=tools,
            system_prompt=GMAIL_AGENT_PROMPT
        )

        # Store the agent in the session so we can use it in 'on_message'
        cl.user_session.set("agent", agent_executor)

        await cl.Message(content="🤖 **Gmail Cleanup Agent at your Service!**\n\nYou can say things like: \n- *'Find emails from Upwork'* \n- *'Delete all newsletters'*").send()

    except Exception as e:
        await cl.Message(content=f"❌ **Error initializing Agent:** {e}").send()


@cl.on_message
async def main(message: cl.Message):
    """
    Handles user messages and runs the agent.
    """
    agent = cl.user_session.get("agent")
    
    if not agent:
        await cl.Message(content="⚠️ Agent not initialized. Please refresh the page to restart.").send()
        return

    # Run the agent with callback handlers to stream the "thought process" to the UI
    res = await agent.ainvoke(
        {"messages": [("user", message.content)]},
        config={"callbacks": [cl.LangchainCallbackHandler()]}
    )
    
    # Extract the final response text from the last AI message
    messages = res.get("messages", [])
    if messages:
        response_text = messages[-1].content
    else:
        response_text = "✅ Task completed."
    
    await cl.Message(content=response_text).send()
