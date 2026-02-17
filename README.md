# ADK Multi-Agent Streamlit App

A production-style starter project that combines:
- Google ADK multi-agent backend (`research_team`)
- Streamlit frontend with login (username + PIN)
- Per-user persistent chat history
- One-command launcher for ADK API server + Streamlit

## Features

- Multi-agent ADK setup with coordinator + specialist agents
- User allowlist from `users.json`
- PIN validation (defaults to `111111` when PIN is omitted)
- Per-user saved chat history in `data/chat_history/`
- ADK API compatibility handling (`/run` and `/run_sse` payload variants)
- Graceful handling for Gemini quota/rate limit errors (429)

## Project Structure

```text
.
|-- launcher.py
|-- streamlit_app.py
|-- requirements.txt
|-- research_team/
|   |-- __init__.py
|   `-- agent.py
|-- users.json
|-- .env.example
`-- data/
    `-- chat_history/
```

## Prerequisites

- Python 3.10+
- Google ADK compatible environment
- Gemini API key

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Update `.env`:

```env
GOOGLE_API_KEY=your_google_api_key
ADK_MODEL=gemini-2.5-flash
```

Run:

```powershell
python launcher.py
```

This starts:
1. `adk api_server`
2. Streamlit UI at `http://localhost:8501`

## Configure Users

Edit `users.json`:

```json
{
  "users": [
    { "username": "alice@company.com", "display_name": "Alice", "pin": "248163" },
    { "username": "bob@company.com", "display_name": "Bob" }
  ]
}
```

Notes:
- `pin` is optional.
- If omitted, default PIN is `111111`.
- Username + PIN must match to login.

## Running Manually

Terminal 1:

```powershell
adk api_server
```

Terminal 2:

```powershell
streamlit run streamlit_app.py
```

## Common Issues

- `Session not found`: handled automatically by session creation before `/run`.
- `429 RESOURCE_EXHAUSTED`: quota/rate limit reached for current Gemini plan.
- `users.json BOM error`: loader supports BOM via `utf-8-sig`.

## GitHub Notes

Before pushing:
- Keep `.env` out of git (already in `.gitignore`).
- Review `users.json` for sensitive data.
- Optionally replace `users.json` with placeholders.

## License

Add your preferred license (for example MIT) before publishing.
