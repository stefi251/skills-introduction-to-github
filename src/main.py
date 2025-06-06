from dotenv import load_dotenv
load_dotenv()
import os
import time
import tempfile
import openai
import csv
from datetime import datetime

from fastapi import FastAPI, File, UploadFile, Form, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# Settings from OpenAI account
#openai.api_key = os.environ['OPENAI_API_KEY']
REGADAM_ID = "asst_v6GxBkutMQQGxK5qFfFs5l8x"
VECTOR_STORE_ID = "vs_67f25a42178081919b5f962f80612121"
LOG_FILE = "chat_metrics.csv"

openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError(
        "Please set OPENAI_API_KEY (in your environment or a .env file)"
    )

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def html_form():
    return """
    <html><body style="font-family:sans-serif">
    <h2>Regada Valve Assistant</h2>
    <form action="/ask" enctype="multipart/form-data" method="post">
    <textarea name="question" rows="4" cols="50" placeholder="Describe your issue/question"></textarea><br>
    Optional file: <input type="file" name="file"><br>
    <input type="hidden" name="thread_id" value="">
    <input type="submit" value="Ask">
    </form>
    </body></html>
    """

@app.post("/ask")
async def ask(
    request: Request,
    question: str = Form(...),
    file: UploadFile = File(None),
    thread_id: str = Form(None),
):
    attached_file_id = None
    if file is not None:
        contents = await file.read()
        if contents:
            tmp_path = tempfile.mktemp(suffix=os.path.splitext(file.filename)[-1])
            with open(tmp_path, "wb") as f:
                f.write(contents)
            oai_file = openai.files.create(file=open(tmp_path, "rb"), purpose="assistants")
            attached_file_id = oai_file.id
            os.remove(tmp_path)

    # New conversation?
    if not thread_id:
        run = openai.beta.threads.create_and_run(
            assistant_id=REGADAM_ID,
            thread={"messages": [{"role": "user", "content": question}]},
            tool_resources={"file_search": {"vector_store_ids": [VECTOR_STORE_ID]}},
        )
        thread_id = run.thread_id
    else:
        openai.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=question,
        )
        run = openai.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=REGADAM_ID
        )

    # Poll until done
    while True:
        status = openai.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id,
        ).status
        if status in ("completed", "failed"):
            break
        time.sleep(1)

    # Fetch messages (history)
    msgs = openai.beta.threads.messages.list(thread_id)
    # Sorted by creation time
    msgs_sorted = sorted(msgs.data, key=lambda m: m.created_at)

    # Extract the last assistant message (the answer)
    answer_msg = next(
        (m for m in reversed(msgs_sorted)
         if m.role == "assistant" and hasattr(m.content[0], "text")),
        None,
    )
    answer_text = answer_msg.content[0].text.value if answer_msg else "[No answer returned]"

    # ----- 1. DOCUMENT/MANUAL REFERENCES (CITATIONS) -----
    citations_html = ""
    if answer_msg:
        file_citations = set()
        for part in answer_msg.content:
            # For each content part, check for text and annotations
            if hasattr(part, "text") and hasattr(part.text, "annotations") and part.text.annotations:
                for ann in part.text.annotations:
                    if getattr(ann, 'type', None) == "file_citation":
                        file_id = ann.file_citation.file_id
                        try:
                            file_info = openai.files.retrieve(file_id)
                            filename = file_info.filename
                        except Exception:
                            filename = f"File ID {file_id}"
                        # Some assistants return the quote text, some just indices. Adjust if needed.
                        quote_part = getattr(ann.file_citation, "quote", "")
                        file_citations.add(f"{filename}" + (f" (\"{quote_part}\")" if quote_part else ""))
        if file_citations:
            citations_html = "<h4>References:</h4><ul>" + "".join(
                f"<li>{c}</li>" for c in file_citations) + "</ul>"

    # ----- 2. LOG INTERACTION METRICS -----
    # Save each Q/A to CSV for admin analysis
    log_row = [datetime.now().isoformat(), thread_id, question, answer_text]
    try:
        with open(LOG_FILE, "a", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(log_row)
    except Exception as e:
        print(f"Failed to log chat: {e}")

    # ----- 3. CHAT HISTORY -----
    chat_html = ""
    for m in msgs_sorted:
        if hasattr(m.content[0], "text"):
            who = m.role.title()
            text = m.content[0].text.value
            chat_html += f"<b>{who}:</b> {text}<br>"

    # ----- 4. CONVERSATION SUMMARY -----
    # Not called every time, only show as button in escalation flow!

    # ---- Render response page ----
    return HTMLResponse(content=f"""
    <html><body style="font-family:sans-serif">
    <h2>Regada Valve Assistant</h2>
    <h3>Conversation so far:</h3>
    <div style='background:#f1f1f1;padding:10px;max-width:700px'>{chat_html}</div>
    <hr>
    <h3>Your latest question:</h3><p>{question}</p>
    <h3>Assistant Response:</h3><div style='background:#dde;padding:8px;max-width:700px'>{answer_text}</div>
    {citations_html}
    <form action="/ask" enctype="multipart/form-data" method="post">
        <textarea name="question" rows="4" cols="50" placeholder="Continue conversation"></textarea><br>
        Optional file: <input type="file" name="file"><br>
        <input type="hidden" name="thread_id" value="{thread_id}">
        <input type="submit" value="Ask">
    </form>
    <form action="/escalate" method="post" style="margin-top:10px">
        <input type="hidden" name="thread_id" value="{thread_id}">
        <button type="submit" style="background:#d44;color:white;padding:6px 16px">Escalate to Human Support</button>
    </form>
    <a href='/'>New Conversation</a> | <a href='/stats'>Admin Stats</a>
    </body></html>
    """)

# ----- ESCALATION ENDPOINT -----
@app.post("/escalate")
async def escalate(thread_id: str = Form(...), background_tasks: BackgroundTasks = None):
    # Get full chat history for the thread
    msgs = openai.beta.threads.messages.list(thread_id)
    msgs_sorted = sorted(msgs.data, key=lambda m: m.created_at)
    chat_history_lines = []
    for m in msgs_sorted:
        if hasattr(m.content[0], "text"):
            who = m.role.title()
            text = m.content[0].text.value
            chat_history_lines.append(f"{who}: {text}")

    thread_text = "\n".join(chat_history_lines)
    summary_text = ""

    # Try to auto-generate a summary (optional!)
    try:
        openai.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content="Please summarize the conversation so far."
        )
        run = openai.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=REGADAM_ID,
        )
        while True:
            status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id).status
            if status == "completed":
                break
            time.sleep(1)
        new_msgs = openai.beta.threads.messages.list(thread_id)
        summary_msg = next(
            (m for m in reversed(new_msgs.data) if m.role == "assistant" and hasattr(m.content[0], "text")),
            None
        )
        if summary_msg:
            summary_text = summary_msg.content[0].text.value
    except Exception as e:
        summary_text = "[Summary error or not available]"

    # The escalation - send to Email (or Slack, etc)
    escalation_note = f"Escalated conversation (Thread {thread_id}):\n\n"
    escalation_note += f"Summary:\n{summary_text}\n\nFull chat history:\n{thread_text}\n"

    # ----------- SMTP BLOCK (fill in with your SMTP info, uncomment when ready) -----------
    # import smtplib
    # sender = "from@example.com"
    # receiver = "to@example.com"
    # message = f"""Subject: Escalated OpenAI Assistant Chat\n\n{escalation_note}"""
    # def _send_mail():
    #     with smtplib.SMTP("smtp.example.com", 587) as server:
    #         server.starttls()
    #         server.login("username", "password")
    #         server.sendmail(sender, receiver, message)
    # background_tasks.add_task(_send_mail)

    # For demo, just show the chat/summary as confirmation
    return HTMLResponse(content=f"""
    <html><body style="font-family:sans-serif">
    <h2>Escalation Submitted</h2>
    <h4>Summary:</h4>
    <div style='background:#efc;padding:8px'>{summary_text}</div>
    <h4>Full Chat History:</h4>
    <pre style="background:#f1f1f1;padding:10px">{thread_text}</pre>
    <a href='/'>Start new conversation</a>
    </body></html>
    """)

# ----- ADMIN/PERFORMANCE METRICS -----
@app.get("/stats")
async def stats():
    import pandas as pd
    try:
        df = pd.read_csv(LOG_FILE, names=["datetime", "thread_id", "question", "answer"], encoding="utf-8")
        n_chats = df.groupby("thread_id").size().shape[0]
        n_questions = df.shape[0]
        recent = df.tail(10).to_html()
        return HTMLResponse(content=f"""
        <html><body style="font-family:sans-serif">
        <h2>Chat Assistant Stats</h2>
        <p><b>Total chats:</b> {n_chats}</p>
        <p><b>Total questions:</b> {n_questions}</p>
        <h3>Recent Questions:</h3>
        {recent}
        <a href='/'>Back to Assistant</a>
        </body></html>
        """)
    except Exception as e:
        return HTMLResponse(content=f"<p>No data or error: {e}</p>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
