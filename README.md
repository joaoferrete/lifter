# lifter

[![CI](https://github.com/joaoferrete/lifter/actions/workflows/ci.yml/badge.svg)](https://github.com/joaoferrete/lifter/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Personal Hevy workout client with analytics, goal tracking, Google Fit integration, and an AI coach that remembers your conversations.

---

## What it does

| Feature | Details |
|---|---|
| **Sync** | Fetches your full Hevy workout history locally. Every sync shows a report with new workouts, PRs set, training streak, and volume vs last week. |
| **Analytics** | Volume per muscle group, exercise progression (e1RM), personal records, plateau detection. |
| **Goals** | Set lift targets, frequency goals, body weight / fat / volume targets. Multiple goals of any type coexist. Tracks progress automatically after every sync. |
| **AI Report** | One-shot coaching report: Training / Health / Combined scores (0–100), volume distribution by muscle group and by individual muscle, strengths, weaknesses, recommendations, and a complete routine tailored to your goals. |
| **AI Chat** | Interactive coach that knows your full history, can push routines to Hevy, and can update your goals — all with your approval. Chat is the first option in the main menu. |
| **Snapshot** | At-a-glance panel shown before every menu: last report scores, volume split by muscle group, and all goal progress bars. |
| **Memory** | After every chat the AI extracts key insights (injuries, preferences, feedback) and saves them. Future sessions start with that context already loaded. |
| **Google Fit** | Syncs sleep, steps, calories, and resting HR. Recovery score shown in the header and used in AI suggestions. |
| **Profiles** | Multiple independent profiles on the same machine — each with its own workout history, goals, memories, Hevy account, and Google Fit connection. |
| **Settings** | Weight units (kg / lbs), goal check-in frequency, auto-sync on startup, default stats window, display name, Hevy API key, AI provider settings, debug logging — all configurable through the menu. |
| **Multi-model** | Works with Gemini (default), Claude, OpenRouter, Groq, GitHub Models, or Amazon Bedrock — swap with one env variable. |
| **Debug logs** | Toggleable structured logging to `logs/debug-YYYY-MM-DD.log` covering sync, AI, goals, settings, profile, and error events. |

---

## Requirements

- Python 3.11+
- [Hevy Pro](https://hevy.com) subscription (API access is Pro-only)
- One of: Gemini API key, Anthropic API key, OpenRouter API key, Groq API key, GitHub token, or AWS credentials
- (Optional) Google account with Fitness data

---

## Setup

### 1. Install dependencies

**Option A — directly (system Python):**

```bash
cd lifter
pip install -r requirements.txt
```

**Option B — inside a virtual environment (recommended to avoid conflicts with other Python projects):**

```bash
cd lifter
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Activate the venv with `source .venv/bin/activate` each time you open a new terminal before running `python3 cli.py`. To deactivate, run `deactivate`.

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in your AI provider key (see sections below). Your Hevy API key is **not** set here — it is entered when you first run Lifter and stored per-profile.

### 3. Get your Hevy API key

1. Log in at [hevy.com](https://hevy.com)
2. Go to **Profile → Settings → Developer**
3. Copy your API key

Lifter will ask for it on first run. You can also update it later via **Settings → Profile → Hevy API key**.

### 4. Set up an AI provider

**Option A — Gemini (recommended to start, has a free tier):**

1. Go to [aistudio.google.com](https://aistudio.google.com) and create an API key
2. Add to `.env`:

```
AI_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
# AI_MODEL=gemini-2.5-flash   ← optional, defaults to gemini-2.5-pro
```

**Option B — Claude (Anthropic):**

1. Create an account at [console.anthropic.com](https://console.anthropic.com)
2. Generate an API key
3. Add to `.env`:

```
AI_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-your-key-here
# AI_MODEL=claude-sonnet-4-6   ← optional, defaults to claude-opus-4-8
```

> **Note:** Claude Code Pro and claude.ai subscriptions do not include API access. You need a separate API account at console.anthropic.com.

**Option C — OpenRouter (access to many models through one API key):**

1. Create an account at [openrouter.ai](https://openrouter.ai) and generate an API key
2. Add to `.env`:

```
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key-here
# AI_MODEL=anthropic/claude-3-5-sonnet   ← optional, defaults to anthropic/claude-3-5-sonnet
```

**Option D — Groq (fast inference, free tier available):**

1. Create an account at [console.groq.com](https://console.groq.com) and generate an API key
2. Add to `.env`:

```
AI_PROVIDER=groq
GROQ_API_KEY=your-key-here
# AI_MODEL=llama-3.3-70b-versatile   ← optional, defaults to llama-3.3-70b-versatile
```

**Option E — GitHub Models (free if you have a GitHub account):**

1. Go to [github.com/marketplace/models](https://github.com/marketplace/models) and generate a personal access token
2. Add to `.env`:

```
AI_PROVIDER=github
GITHUB_TOKEN=your-token-here
# AI_MODEL=gpt-4o   ← optional, defaults to gpt-4o
```

**Option F — Amazon Bedrock:**

Requires an AWS account with Bedrock model access enabled. Credentials are read from the environment using the standard AWS credential chain (IAM role, `~/.aws/credentials`, or explicit keys).

1. Install the Bedrock extras: `pip install "anthropic[bedrock]"` (only needed for Claude models on Bedrock)
2. Add to `.env`:

```
AI_PROVIDER=bedrock
AWS_REGION=us-east-1
# For Claude models:
# AI_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0   ← default
# For non-Claude models (Llama, Mistral, etc.) use their Bedrock model ID.

# Explicit credentials (optional — omit if using IAM role or aws configure):
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# AWS_SESSION_TOKEN=      ← only needed for temporary credentials
```

### 5. Run the initial sync

```bash
python3 cli.py
```

The interactive menu opens. Select **Sync new workouts → Full** to download your entire Hevy history.

---

## Install system-wide (optional)

After completing the setup above you can make `lifter` available as a global command so you can run it from any directory without typing `python3 cli.py`.

This uses [pipx](https://pipx.pypa.io), which installs Python applications into isolated environments and exposes their commands on your `PATH`. The source code stays in this directory — the global command just points here.

### Install

```bash
make install
```

`lifter` is now available everywhere. Profiles and databases are stored under the `profiles/` directory inside the project folder — no matter which directory you run `lifter` from, it always uses the same profiles.

### Update

After pulling new changes:

```bash
pipx reinstall lifter
```

Or equivalently:

```bash
make uninstall && make install
```

### Uninstall

```bash
make uninstall
```

> **Note:** `pipx` installs commands to `~/.local/bin`. Make sure that directory is on your `PATH` (most modern shells include it by default). If `lifter` is not found after install, add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile and reload it.

---

## Profiles

Lifter supports multiple independent profiles on the same machine — useful if two athletes share a computer, or if you want to track separate training blocks.

Each profile has its own:
- Workout history, body measurements, and personal records
- Goals and AI coach memories
- Hevy account (API key)
- Google Fit connection

Profile data lives in `profiles/{slug}/` inside the project folder. On first run Lifter asks for your name and Hevy API key to create your initial profile.

### Managing profiles

All profile actions are in **Settings → Profiles**:

| Action | Description |
|---|---|
| Switch profile | Changes the active profile and restarts the app |
| Create new profile | Adds a profile with a new name and Hevy API key |
| Rename current | Updates the display name |
| Update Hevy API key | Changes the stored API key for the active profile |
| Delete a profile | Permanently removes the profile and all its data |

### Migrating from a previous version

If you have an existing `hevy.db` file at the project root, Lifter will automatically migrate it to a named profile on the first run after upgrading.

---

## Google Fit setup (optional)

Adds sleep, steps, calories, and heart rate data to your analytics and AI context.

### Step 1 — Create OAuth credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. **APIs & Services → Library** → search for **Fitness API** → Enable it
4. **APIs & Services → OAuth consent screen**
   - User type: External
   - Fill in app name (anything, e.g. "lifter")
   - Add your Gmail as a **Test user** → Save
5. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything
   - Click Create
6. **Download JSON** → Google will download a file named something like
   `client_secret_123456789-abcdefg.apps.googleusercontent.com.json`  
   **Rename it to `fit_credentials.json`** and place it in the `lifter/` project folder

### Step 2 — Authenticate

In the menu: **Google Fit → Connect / re-authenticate**

A browser window opens. Sign in with the Gmail you added as a test user and approve the fitness permissions. The token is saved as `fit_token.json` and reused automatically.

### Step 3 — Sync

**Google Fit → Sync health data → 30 days**

After syncing, the recovery score appears in the header and the AI coach uses your sleep and HR data in all suggestions.

### Samsung Health users

Samsung Health syncs to Google Fit by default on Android. Enable it in the Samsung Health app under **Settings → Connected services → Google Fit**.

---

## Menu reference

Run `python3 cli.py` to open the interactive menu.

```
  Sync new workouts
  Chat with coach
  ─────────────────────
  My goals
  Dashboard & stats
  Exercise progression
  Personal records
  ─────────────────────
  AI coaching report
  Google Fit  (sleep, steps, HR)
  ─────────────────────
  Settings
  Exit
```

> **Quick view panel** — shown above the menu on every launch: last AI report scores (Training / Health / Overall), volume split by muscle group, and compact goal progress bars.

### Sync new workouts

Downloads workout data from Hevy. After every sync:
- **Workout cards** show each new session with exercises, weights, and **★ PR** badges
- **Training streak** with fire emojis
- **Volume bar chart** vs last week with % change per muscle group
- **Goal progress** panel with bars for all active goals

Choose **Incremental** (default, only fetches changes) or **Full** (re-downloads everything).

### Chat with coach

Interactive conversation with the AI. The coach has full access to:
- Your training history and analytics
- Your active goals (with IDs for modifications)
- Google Fit recovery data
- Memories from all previous conversations

**What the coach can do during chat:**

| Action | How to trigger |
|---|---|
| Answer questions about your training | Just ask |
| Create and push a routine to Hevy | "Create a push day for me" |
| Update an existing Hevy routine | "Update my push day routine" |
| Add a new goal | "Add a goal to deadlift 180kg" |
| Update a goal | "Change my bench goal to 130kg" |
| Remove a goal | "Remove my weight loss goal" |

All goal changes and routine pushes require your explicit confirmation before anything is saved. Pushed routines include a `✦ Powered by Lifter` note in the routine description.

**After the conversation ends**, the AI analyses the full transcript and extracts memorable facts (injuries mentioned, exercise preferences, feedback on suggestions, lifestyle context). These are saved and automatically included in all future sessions.

### My goals

Set and track training goals:

| Goal type | Example |
|---|---|
| Lift PR | Bench Press — 120 kg |
| Frequency | Train 4× per week |
| Weight loss / gain | Reach 75 kg body weight |
| Body fat | Reach 12% body fat |
| Volume | Chest — 15 sets/week |
| Custom | Free-text goal (AI tracks qualitatively) |

Multiple goals coexist — you can have a lift PR goal, a frequency goal, a custom goal, and body composition goals all active at the same time.

**First run**: the wizard runs automatically to set your goals.  
**Check-in**: configurable frequency (7 / 14 / 30 days) — the app asks if your goals are still the same.  
**Progress bars**: shown in the Quick view panel and after every sync (green ≥80%, yellow ≥50%, red <50%, ★ when achieved).

Weight input in the goals wizard follows your units preference (kg or lbs) and is stored as kg internally.

### Dashboard & stats

Full analytics for a selectable time period (4 / 8 / 12 / 24 weeks). The default period is saved as a preference so the prompt is skipped on repeated use.

- Workout frequency, average duration, rest days, longest streak
- Volume by muscle group with inline bar chart, sets/week, sessions/week
- Body measurement trends (weight, body fat %)
- Personal records set in the last 30 days
- Plateau warnings for stalled exercises

### Exercise progression

- **Top gainers**: exercises with the highest e1RM improvement over the period
- **Specific exercise**: fuzzy-search any exercise and see a session-by-session progression table with per-session e1RM delta (green/red)

Uses the Epley formula (`weight × (1 + reps/30)`) for estimated 1RM so sets with different rep ranges are comparable.

### Personal records

All-time best set per exercise ranked by estimated 1RM.

### AI coaching report

Analyses your training data against your goals and generates:

**Performance Scores panel:**
- **Training score** (0–100): consistency, progressive overload, balance, plateau avoidance
- **Health score** (0–100): sleep, recovery, resting HR trend — only shown when Google Fit data is present
- **Combined score** (0–100): weighted 70% training + 30% health
- Color-coded: green ≥80 · cyan ≥60 · yellow ≥40 · red <40
- Scores are cached and shown in the Quick view panel on every launch

**Volume Distribution panel:**
- **By muscle group** (Chest, Back, Legs, Shoulders, Arms, Core, Cardio): % of total weekly sets with bar chart
- **By individual muscle**: granular breakdown of every trained muscle

**Analysis:**
- **Strengths**: what's working
- **Weaknesses**: imbalances, underworked muscles, plateaus
- **Recommendations**: 3–5 actionable tips for the next weeks
- **Next focus**: the single most important thing to address
- **Suggested routine**: a complete workout with exercises, sets, reps, and weights matched to your current strength level — ready to push to Hevy

Select the number of weeks to analyse (4 / 8 / 12 / 16). After the report, the app asks if you want to push the routine directly to your Hevy app.

### Google Fit

- **Sync health data**: pulls sleep sessions, daily steps, calories, and heart rate
- **Recovery dashboard**: sleep averages, nightly consistency, resting HR, active minutes
- **Recovery score (0–100)**: composite of recent sleep quality and resting HR trend
  - 80–100: Excellent | 65–79: Good | 45–64: Fair | <45: Poor
- **Connect / re-authenticate**: runs the OAuth browser flow
- **Disconnect**: removes the token (local data stays in the DB)

When connected, the Google Fit menu item shows a ✓ status chip.

### Settings

**Menu → Settings** gives you full control over app behaviour and your local data.

#### Profile

| Setting | Description |
|---|---|
| **Display name** | Your name shown in the header and used by the AI coach |
| **Hevy API key** | The API key for this profile's Hevy account |

#### Preferences

| Setting | Options | Default |
|---|---|---|
| **Weight units** | kg / lbs | kg |
| **Goal check-in frequency** | Every 7 / 14 / 30 days | 7 days |
| **Auto-sync on startup** | on / off | off |
| **Default stats window** | 4 / 8 / 12 / 24 weeks | 8 weeks |
| **Debug logging** | on / off | off |

When **auto-sync** is on, stale Hevy and Google Fit data syncs silently on launch without prompting. When off, you get a confirmation prompt.

Weight units apply everywhere: goal wizard input, dashboard stats, exercise progression, personal records, and the AI routine preview.

#### AI Coach

| Setting | Description |
|---|---|
| **Context mode** | Full (all analytics) or Slim (fewer tokens, faster) |
| **Token counter** | Cumulative input / output / cache-read tokens with cache hit % |
| **Reset token counter** | Zero out the cumulative counters |

#### Reset data

Nothing on Hevy or Google Fit is ever touched — only the local cache on your machine.

| Option | What it clears |
|---|---|
| **Clear coach memories** | Deletes everything extracted from past chat sessions — the coach starts fresh with no prior context |
| **Clear all goals** | Removes all active goals; the wizard runs again on next launch |
| **Clear sync state** | Resets the sync timestamp so the next incremental sync re-downloads all workouts (existing local data is kept) |
| **Wipe everything** | Double-confirmed — deletes `hevy.db` and disconnects Google Fit (`fit_token.json`). Run **Sync → Full** afterwards to restore |

You can also reset individual pieces manually (replace `{slug}` with your profile slug, e.g. `default`):

```bash
# Delete the local database for a profile
rm profiles/{slug}/hevy.db

# Disconnect Google Fit for a profile (keeps DB data)
rm profiles/{slug}/fit_token.json

# Force the next sync to re-download everything
sqlite3 profiles/{slug}/hevy.db "UPDATE sync_state SET value='1970-01-01T00:00:00Z' WHERE key='last_sync';"

# Clear only coach memories
sqlite3 profiles/{slug}/hevy.db "DELETE FROM chat_memories;"
```

---

## Header reference

The panel at the top of every screen shows:

```
┌─ LIFTER · Your Name ─────────────────────────────────────────────────┐
│ Last workout: 2d ago  ·  🔥🔥 5d streak  ·  3 routines               │
│ 145 workouts  ·  3 this week  ·  4.2/wk avg  ·  2 goals              │
│ AI: claude · claude-opus-4-8  ·  Sync ✓ 5m ago  ·  Recovery 82/100  │
└──────────────────────────────────────────────────────────────────────┘
```

- **Sync status**: green ✓ if synced within 24h, yellow ⚠ if stale
- **Routine count**: number of routines saved in Hevy
- **Recovery**: shown when Google Fit is connected and has recent data

---

## Architecture

```
lifter/
├── hevy/
│   ├── client.py        API wrapper (all Hevy endpoints + payload sanitization)
│   └── sync.py          Full + incremental sync via /v1/workouts/events
├── db/
│   ├── store.py         SQLite schema + upsert helpers
│   ├── goals.py         Goal CRUD, progress computation, user preferences
│   └── memories.py      Chat memory: save/load/context
├── fit/
│   ├── auth.py          Google OAuth (InstalledAppFlow, token persistence)
│   ├── client.py        Google Fit REST API (aggregate + sessions)
│   ├── sync.py          Sync sleep and daily stats
│   └── analytics.py     Recovery score, sleep summary, activity summary
├── analytics/
│   ├── volume.py        Weekly tonnage per muscle group
│   ├── progression.py   e1RM progression and plateau detection
│   ├── frequency.py     Workout cadence and session duration
│   └── records.py       Personal records and body measurement trends
├── ai/
│   ├── provider.py      Unified ChatSession abstraction (Gemini, Claude, OpenRouter, Groq, GitHub Models, Bedrock)
│   ├── coach.py         Coaching report (with scores + distribution), chat loop, goal tools, memory extraction
│   └── sanitize.py      Input sanitization and prompt-injection defence
├── cli.py               Interactive menu (questionary + Rich)
├── config.py            .env loader
├── profile_mgr.py       Multi-profile management (create, activate, switch, delete)
├── debug_log.py         Structured debug logging (toggled via Settings → Preferences)
├── profiles/            Per-profile data directories (gitignored)
│   └── {slug}/
│       ├── hevy.db          Local SQLite database
│       ├── fit_token.json   Google OAuth token (created automatically)
│       └── profile.json     Profile name and Hevy API key
└── fit_credentials.json Google OAuth credentials (you create this)
```

---

## Database tables

| Table | Contents |
|---|---|
| `workouts` | Workout metadata |
| `workout_exercises` | Exercises per workout |
| `workout_sets` | Sets per exercise (weight, reps, type) |
| `exercise_templates` | Exercise library with muscle group tags |
| `body_measurements` | Weight, fat %, body measurements by date |
| `fit_sleep` | Sleep session duration by date |
| `fit_daily` | Steps, calories, avg/min HR, active minutes by date |
| `routines` | Workout routines (created by AI or synced from Hevy) |
| `routine_exercises` | Exercises within routines |
| `routine_sets` | Sets within routine exercises |
| `user_goals` | Active and achieved training goals |
| `user_preferences` | Display name, units, auto-sync, default windows, cached scores, and other settings |
| `chat_memories` | Insights extracted from past conversations |
| `sync_state` | Last sync timestamps and other state keys |

---

## Configuration reference

### `.env` (global, AI provider keys only)

```bash
# AI provider — pick one value for AI_PROVIDER, set the matching key
AI_PROVIDER=gemini        # gemini | claude | openrouter | groq | github | bedrock
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
GROQ_API_KEY=
GITHUB_TOKEN=
AI_MODEL=                 # optional — overrides the provider default (see setup section)

# Amazon Bedrock (only when AI_PROVIDER=bedrock)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=        # optional if using IAM role or aws configure
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=        # optional, for temporary credentials

# Google Fit
GOOGLE_CREDENTIALS_FILE=fit_credentials.json   # path to downloaded OAuth JSON
```

### Per-profile config (`profiles/{slug}/profile.json`)

Each profile stores its own settings that are set through the in-app menus:

| Key | Description |
|---|---|
| `name` | Display name shown in menus |
| `slug` | URL-safe identifier used as the directory name |
| `hevy_api_key` | API key for this profile's Hevy account |

Profile databases live at `profiles/{slug}/hevy.db` and Google Fit tokens at `profiles/{slug}/fit_token.json`.

All in-app preferences (units, auto-sync, check-in frequency, etc.) are stored in the `user_preferences` table in each profile's database, not in `.env`.

---

## Debug logs

Lifter can write structured logs to `logs/debug-YYYY-MM-DD.log` (one file per day, inside the project folder). This is useful for diagnosing sync failures, AI errors, and profile events without having to instrument the code manually.

### Enabling

**Settings → Preferences → Debug logging → on**

The setting takes effect immediately — no restart needed. The log path is printed to the terminal when you enable it.

### What is logged

| Category | Events |
|---|---|
| `[APP]` | App startup (profile, provider, model) and exit |
| `[MENU]` | Every main menu selection; stats period; progression type; goals action |
| `[SYNC]` | Hevy full / incremental sync start and complete (counts); Google Fit sync start and complete; auto-sync trigger; user accept / decline of sync prompts; manual sync type chosen; errors |
| `[GOAL]` | Each goal type created; wizard completed (total goals); weekly check-in triggered and result (confirmed / updated / skipped); goals cleared |
| `[AI]` | Chat session started (provider, model, weeks, slim mode, language) and ended (turn count); coaching report start and complete (token delta); every tool call (push\_routine / update\_routine / manage\_goals); routine and goal-change confirmed or declined by user; memories extracted count; token usage per request |
| `[SETTING]` | Every user-initiated settings change — units, check-in frequency, auto-sync, stats window, debug toggle, display name, Hevy API key, AI context mode, language, token counter reset, Google Fit connect / disconnect |
| `[PROFILE]` | Profile created, activated, renamed, deleted, switched; startup selection; first-run setup; data migration |
| `[RESET]` | Memories cleared; goals cleared; sync state reset; full data wipe |
| `[ERROR]` | AI API errors (provider, model, HTTP status, traceback line); Google Fit auth and sync failures; routine push / update failures; goal-change failures; coaching report failures |

> Personal data is never written to logs: goal descriptions, body targets, chat messages, and per-session token counts are all omitted.

### Example

```
2026-06-05 13:41:00 [APP    ] Lifter started  profile=alice  provider=gemini  model=gemini-2.5-pro
2026-06-05 13:41:01 [SYNC   ] Data is fresh, no sync needed
2026-06-05 13:41:05 [MENU   ] Selected: chat
2026-06-05 13:41:06 [AI     ] Chat session started  provider=gemini  model=gemini-2.5-pro  weeks=8  slim=True
2026-06-05 13:41:45 [AI     ] Token usage  provider=gemini  model=gemini-2.5-pro  input=1234  output=567  cache_read=890
2026-06-05 13:42:10 [AI     ] Tool call: push_routine
2026-06-05 13:42:12 [AI     ] Routine pushed to Hevy  routine_id=abc123  exercises=6
2026-06-05 13:42:30 [AI     ] Chat session ended  turns=4
2026-06-05 13:43:25 [ERROR  ] ClientError: 403 PERMISSION_DENIED  provider=gemini  status=403
2026-06-05 13:44:00 [SETTING] auto_sync changed  value=1
2026-06-05 13:44:10 [APP    ] Lifter exited
```

Logs are never committed — `logs/` is in `.gitignore`.

---

## Internationalization

Lifter supports multiple UI languages. The translation layer lives in `i18n.py` and reads JSON files from `locales/`.

### Changing the language

**Per profile (recommended):** Go to **Settings → Preferences → UI language** and pick from the list. The change takes effect immediately for the rest of the session and persists across restarts.

**Global bootstrap language** (shown before a profile is selected): set `DEFAULT_LANGUAGE=en` in your `.env` file. This controls the language of the profile-selector screen.

### Supported languages

| Code | Name |
|------|------|
| `en` | English |
| `pt_BR` | Português (Brasil) |

### Adding a new language

1. **Copy the English locale file and translate it:**

   ```bash
   cp locales/en.json locales/fr.json
   # edit locales/fr.json — translate every value, keep keys and {placeholders} intact
   ```

   Rules for translators:
   - **Keys** (`"menu.sync"`) — never translate, only values.
   - **`{placeholders}`** — keep them verbatim; they are filled at runtime (e.g. `{retry_after}`, `{name}`).
   - **Rich markup** (`[bold]`, `[red]...[/red]`, `[dim]`) — preserve tags exactly; they control terminal formatting.
   - Empty string values fall back to English automatically.

2. **Register the language code** in `i18n.py`:

   ```python
   _SUPPORTED: set = {"en", "pt_BR", "fr"}   # add your code here
   ```

3. **Add a display name** to `_UI_LANGUAGES` in `cli.py`:

   ```python
   _UI_LANGUAGES = [
       ("en",    "English"),
       ("pt_BR", "Português (Brasil)"),
       ("fr",    "Français"),           # add this line
   ]
   ```

4. **Verify** the new locale loads:

   ```bash
   python -c "from i18n import _; import i18n; i18n.init('fr'); print(_('menu.sync'))"
   ```

   If a key is missing from your locale file, Lifter falls back to English automatically — no crash.

---

## Running tests

```bash
python -m pytest tests/
```

The test suite uses an in-memory SQLite database per test — no real `hevy.db` is touched. All AI provider calls and external HTTP requests are mocked.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, branch naming, commit format, CI checks, and the PR template.
