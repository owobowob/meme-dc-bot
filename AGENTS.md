# Repository Guidelines

## Project Structure

- `main.py` starts the Discord bot, initializes SQLite, and syncs slash commands. `config.py` loads environment variables and defines defaults.
- `cogs/` handles Discord commands, input validation, and permissions. Keep tag extraction, search, persistence, and shared Discord response logic in `services/`. The meme record lives in `models/meme.py`.
- `data/known_tags.json` stores the tag catalog. The bot creates `data/memes.db` at runtime by default. `scripts/` contains import and maintenance utilities and a live tag-extraction CLI. Automated tests live in `tests/`.

## Development Commands

Use Python 3.11 or newer. Run these commands from the repository root in PowerShell.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m pytest -q
```

The first two commands create a virtual environment and install dependencies. The third runs the bot. The last runs the test suite. Before starting the bot, copy `.env.example` to `.env` if needed, then set `DISCORD_BOT_TOKEN` and the selected LLM provider’s API key. Set `DISCORD_TEST_GUILD_ID` to sync commands to a test server.

## Coding Style

Use four spaces for indentation, `snake_case` for modules, functions, and variables, and `PascalCase` for classes. Keep meme tags in lowercase `snake_case`, such as `this_is_fine`. Follow nearby type annotations and preserve the async interface of Discord command handlers. The repository has no configured formatter or linter.

## Testing

Name pytest modules `test_<module>.py` and test functions `test_<behavior>`. Mock Discord, LLM, and HTTP calls, and use pytest’s `tmp_path` for SQLite tests. Run a focused check by appending a path such as `tests/test_database.py` to the pytest command. There is no configured coverage threshold. `scripts/test_extract_tags.py` calls a live provider and requires its API key.

## Commits and Pull Requests

Git history is unavailable in this checkout. Use short, imperative commit subjects such as `Fix duplicate meme handling`. In pull requests, describe the behavior change, list the checks you ran, and link the issue when applicable. Include a Discord screenshot when the visible response changes. Keep `.env`, credentials, and local SQLite files out of commits.
