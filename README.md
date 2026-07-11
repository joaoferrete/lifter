# lifter

[![CI](https://github.com/joaoferrete/lifter/actions/workflows/ci.yml/badge.svg)](https://github.com/joaoferrete/lifter/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Personal Hevy workout client with analytics, goal tracking, Google Fit integration, and an AI coach that remembers your conversations.

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install](#install)
- [Where Lifter stores data](#where-lifter-stores-data)
- [Setup](#setup)
- [Install system-wide from source (optional)](#install-system-wide-from-source-optional)
- [Profiles](#profiles)
- [Google Fit setup (optional)](#google-fit-setup-optional)
- [Menu reference](#menu-reference)
- [Header reference](#header-reference)
- [Configuration reference](#configuration-reference)
- [Debug logs](#debug-logs)
- [Internationalization](#internationalization)
- [License](#license)
- [Contributing](#contributing)

---

## What it does

| Feature | Details |
|---|---|
| **Sync** | Fetches your full Hevy workout history locally. Every sync shows a report with new workouts, PRs set, training streak, and volume vs last week. |
| **Analytics** | Volume per muscle group, exercise progression (e1RM), personal records, plateau detection. |
| **Goals** | Set lift targets, frequency goals, body weight / fat / volume targets. Multiple goals of any type coexist. Tracks progress automatically after every sync — body-composition goals show **initial → current → target** and progress can go **negative** when you move the wrong way. |
| **Body measurements** | Log your current weight and body-fat % manually (no Google Fit required) from the **Body measurements** menu item. Stores your height per profile and shows your **BMI** in the snapshot and stats. |
| **AI Report** | One-shot coaching report: Training / Health / Combined scores (0–100), volume distribution by muscle group and by individual muscle, strengths, weaknesses, recommendations, and a complete routine tailored to your goals. |
| **AI Chat** | Interactive coach that knows your full history, can push routines to Hevy, and can update your goals — all with your approval. Chat is the first option in the main menu. |
| **Snapshot** | At-a-glance panel shown before every menu: last report scores, volume split by muscle group, latest weight / body-fat / BMI, and all goal progress bars. |
| **Memory** | After every chat the AI extracts key insights (injuries, preferences, feedback) and saves them. Future sessions start with that context already loaded. |
| **Google Fit** | Syncs sleep, steps, calories, and resting HR. Recovery score shown in the header and used in AI suggestions. |
| **Profiles** | Multiple independent profiles on the same machine — each with its own workout history, goals, memories, Hevy account, and Google Fit connection. |
| **Settings** | Weight units (kg / lbs), height, goal check-in frequency, auto-sync on startup, staleness threshold, default stats window, display name, Hevy API key, AI provider/model, privacy toggles, token budget, coach memories — all configurable through the menu. |
| **Multi-model** | Works with Gemini (default), Claude, OpenRouter, Groq, GitHub Models, or Amazon Bedrock — swap in the app (Settings → AI Coach) or via env variable. |
| **Developer tools** | Export/import your data as JSON, preview the exact AI context, database row counts, last sync status, debug logging with automatic rotation (newest 14 files kept). |

---

## Requirements

- Python 3.11+
- [Hevy Pro](https://hevy.com) subscription (API access is Pro-only)
- One of: Gemini API key, Anthropic API key, OpenRouter API key, Groq API key, GitHub token, or AWS credentials
- (Optional) Google account with Fitness data

---

## Install

The recommended install is via [pipx](https://pipx.pypa.io), straight from PyPI:

```bash
pipx install lifter-cli
lifter
```

Updates are **not** automatic — when a new version comes out, upgrade with:

```bash
pipx upgrade lifter-cli
```

> **Why pipx and not plain `pip`?** The package currently ships top-level modules;
> pipx's isolated virtualenv keeps them from ever clashing with other packages.
> Also note the PyPI name is **`lifter-cli`** — `pip install lifter` is an
> unrelated, abandoned package.

Upgrading from an old editable install? Run `pipx uninstall lifter` (the old
distribution name) before `pipx install lifter-cli`. Your data is safe — it
lives in your user folders, not in the package (see
[Where Lifter stores data](#where-lifter-stores-data)).

---

## Where Lifter stores data

Everything writable lives in standard user directories (XDG), so updates and
reinstalls never touch your data:

| Contents | Location | Override |
|---|---|---|
| Profiles, databases, exports | `~/.local/share/lifter/` | `$XDG_DATA_HOME` |
| `.env` (API keys), `fit_credentials.json` | `~/.config/lifter/` | `$XDG_CONFIG_HOME` |
| Debug logs, chat history | `~/.local/state/lifter/` | `$XDG_STATE_HOME` |

Setting `LIFTER_HOME=/some/dir` forces all three into a single directory
(portable installs, tests). On the first run after upgrading from an older
version, Lifter automatically moves data from the project folder to these
locations — once — and tells you what moved.

---

## Setup

### 1. Install dependencies (from source only)

If you installed via pipx you can skip this. From a checkout:

```bash
cd lifter
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Activate the venv with `source .venv/bin/activate` each time you open a new terminal before running `python3 cli.py` (or install the global `lifter` command with `make install`). To deactivate, run `deactivate`.

### 2. Configure your AI provider key

The easiest way is **in-app**: run `lifter`, then **Settings → AI Coach → API keys** —
values are stored in `~/.config/lifter/.env` (created with mode 600).

Prefer a file? Edit `~/.config/lifter/.env` directly (use `.env.example` in this
repo as a reference). Your Hevy API key is **not** set here — it is entered when
you first run Lifter and stored per-profile.

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
# AI_MODEL=gemini-2.5-pro   ← optional, defaults to gemini-flash-latest
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
# AI_MODEL=anthropic/claude-opus-4.8   ← optional, defaults to openrouter/owl-alpha
```

**Option D — Groq (fast inference, free tier available):**

1. Create an account at [console.groq.com](https://console.groq.com) and generate an API key
2. Add to `.env`:

```
AI_PROVIDER=groq
GROQ_API_KEY=your-key-here
# AI_MODEL=openai/gpt-oss-120b   ← optional, defaults to openai/gpt-oss-120b
```

> **Note:** Groq deprecated `llama-3.3-70b-versatile` for free/developer tiers (June 2026). The default is now `openai/gpt-oss-120b`, which supports tool calling.

**Option E — GitHub Models (free if you have a GitHub account):**

1. Go to [github.com/marketplace/models](https://github.com/marketplace/models) and generate a fine-grained personal access token with the **`models:read`** permission
2. Add to `.env`:

```
AI_PROVIDER=github
GITHUB_TOKEN=your-token-here
# AI_MODEL=openai/gpt-4o   ← optional, defaults to openai/gpt-4o
```

> **Note:** GitHub Models uses the `https://models.github.ai/inference` endpoint and requires model names in `publisher/model` format (e.g. `openai/gpt-4o`, `meta/Llama-3.3-70B-Instruct`). A plain `gpt-4o` (no publisher prefix) returns HTTP 400.

**Option F — Amazon Bedrock:**

Requires an AWS account with Bedrock model access enabled. There are two ways to authenticate:

- **Bearer token (Bedrock API key)** — set `AWS_BEARER_TOKEN_BEDROCK`. For **Claude models** this works with **no `boto3` install required**. This is the simplest option if you can't use AWS access keys.
- **AWS credentials (boto3)** — standard AWS credential chain (IAM role, `~/.aws/credentials`, or explicit keys). Requires `pip install "anthropic[bedrock]"` for Claude models, and is required for non-Claude (Converse) models such as Llama or Mistral.

Add to `.env`:

```
AI_PROVIDER=bedrock
AWS_REGION=us-east-1
# For Claude models:
# AI_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0   ← default (US inference profile)
#   Claude models on Bedrock require a cross-region inference profile, not the
#   bare model ID — using "anthropic.claude-…" directly returns a 400. Match the
#   prefix to your region: us.* for US regions, eu.* for EU, apac.* for APAC.
# For non-Claude models (Llama, Mistral, etc.) use their Bedrock model ID.

# Auth option A — bearer token (Claude models need no boto3 install):
# AWS_BEARER_TOKEN_BEDROCK=your-bedrock-api-key

# Auth option B — AWS credentials (omit if using IAM role or aws configure):
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# AWS_SESSION_TOKEN=      ← only needed for temporary credentials
```

> A bearer token and AWS credentials are mutually exclusive on the Claude path — set one or the other, not both.

### 5. Run the initial sync

```bash
lifter               # from a source checkout without `make install`: python3 cli.py
```

The interactive menu opens. Select **Sync new workouts → Full** to download your entire Hevy history.

---

## Install system-wide from source (optional)

If you work from a checkout (instead of `pipx install lifter-cli`), you can still expose `lifter` as a global command pointing at your working tree:

```bash
make install          # pipx install --editable .
```

Data location is identical either way — user folders, not the project dir (see [Where Lifter stores data](#where-lifter-stores-data)).

### Update

`make install` uses an **editable** install, so after pulling new changes the
`lifter` command already runs the updated code. Re-run `make install` only if
dependencies changed. (PyPI installs update with `pipx upgrade lifter-cli` —
see [Install](#install).)

### Uninstall

```bash
make uninstall        # pipx uninstall lifter-cli
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

Profile data lives in `~/.local/share/lifter/profiles/{slug}/`. On first run Lifter asks for your name and Hevy API key to create your initial profile.

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

Both migrations happen automatically, once, on the first run after upgrading:

| Old location (project folder) | New location |
|---|---|
| `profiles/`, `profiles.json` | `~/.local/share/lifter/` |
| `.env` | `~/.config/lifter/.env` |
| `fit_credentials.json` | `~/.config/lifter/` |
| `logs/` | `~/.local/state/lifter/logs/` |
| `~/.hevy_chat_history` | `~/.local/state/lifter/chat_history` |

A legacy root `hevy.db` (pre-profiles era) is also migrated into a named profile.

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
   `client_secret_123456789-abcdefg.apps.googleusercontent.com.json` — no need
   to rename or move it; Lifter asks for its location in the next step and
   copies it to `~/.config/lifter/fit_credentials.json`

### Step 2 — Authenticate

In the menu: **Google Fit → Connect / re-authenticate**

Lifter asks for the path to the downloaded JSON (first time only), then a browser window opens. Sign in with the Gmail you added as a test user and approve the fitness permissions. The token is saved per profile as `fit_token.json` and reused automatically.

### Step 3 — Sync

**Google Fit → Sync health data → 30 days**

After syncing, the recovery score appears in the header and the AI coach uses your sleep and HR data in all suggestions.

### Samsung Health users

Samsung Health syncs to Google Fit by default on Android. Enable it in the Samsung Health app under **Settings → Connected services → Google Fit**.

---

## Menu reference

Run `lifter` to open the interactive menu.

```
  Sync new workouts
  Chat with coach
  ─────────────────────
  My goals
  Body measurements
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

> **Quick view panel** — shown above the menu on every launch: last AI report scores (Training / Health / Overall), volume split by muscle group, latest weight / body-fat / BMI, and compact goal progress bars.

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

Type `quit` (or the localized exit words — `sair` in pt-BR) or press Ctrl+C to return to the menu. If a monthly token budget is set, the chat shows a warning at start when you're above 80% (yellow) or 100% (red) of it.

**After the conversation ends**, the AI analyses the full transcript and extracts memorable facts (injuries mentioned, exercise preferences, feedback on suggestions, lifestyle context). These are saved and automatically included in all future sessions — you can review, delete, or cap them under **Settings → AI Coach → Manage memories**, and export them under **Settings → Developer → Export data**.

**Privacy:** what the coach receives is aggregated analytics, recent workouts, goals, and memories — you can exclude your name and body-composition data via the privacy toggles in **Settings → AI Coach**, and inspect the exact payload with **Settings → Developer → Preview AI context** (fully local, no API call).

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

A baseline is captured when a goal is created (including goals you ask the AI coach to add). Body-composition goals (weight / body fat) display **initial → current → target**, and the percentage can go **negative** if you move away from the target — so a regression is visible rather than hidden at 0%.

Weight input in the goals wizard follows your units preference (kg or lbs) and is stored as kg internally.

### Body measurements

**Menu → Body measurements** lets you record your current **weight** and **body-fat %** by hand — useful when you don't use Google Fit. Entries are stored against today's date and immediately feed goal progress and the snapshot.

- On **first run** the app asks for your **height** and **current weight** so progress and BMI work from day one.
- Returning profiles are prompted for a fresh weight only when the latest reading is **stale** (older than your check-in frequency) — never on every launch.
- **Height** is stored per profile and editable under **Settings → Profile**. Input/display respect your unit preference (cm for metric, ft/in for imperial).
- **BMI** (`weight ÷ height²`) is computed on the fly and shown in the snapshot, the stats body table, and the AI coaching context.

### Dashboard & stats

Full analytics for a selectable time period (4 / 8 / 12 / 24 weeks). The default period is saved as a preference so the prompt is skipped on repeated use.

- Workout frequency, average duration, rest days, longest streak
- Volume by muscle group with inline bar chart, sets/week, sessions/week
- Body measurement trends (weight, body fat %, BMI)
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
| **Height** | Your height (per profile), used to compute BMI. Input/display follow your unit preference (cm or ft/in) |

#### Preferences

| Setting | Options | Default |
|---|---|---|
| **Weight units** | kg / lbs | kg |
| **Goal check-in frequency** | Every 7 / 14 / 30 days | 7 days |
| **Auto-sync on startup** | on / off | off |
| **Sync staleness threshold** | 6 / 12 / 24 / 48 hours | 24h |
| **Default stats window** | 4 / 8 / 12 / 24 weeks | 8 weeks |

When **auto-sync** is on, stale Hevy and Google Fit data syncs silently on launch without prompting. When off, you get a confirmation prompt. The **staleness threshold** controls how old data must be before the prompt/auto-sync (and the header ⚠ badge) kicks in.

Weight units apply everywhere: goal wizard input, dashboard stats, exercise progression, personal records, and the AI routine preview.

#### AI Coach

| Setting | Description |
|---|---|
| **AI provider** | Switch between providers in-app (only those with a configured API key are listed); stored per profile, overriding `.env` |
| **Model** | Provider default or any custom model name; stored per profile |
| **API keys** | Enter/clear provider credentials (Gemini, Claude, OpenRouter, Groq, GitHub, AWS/Bedrock) with hidden input; saved to `~/.config/lifter/.env` (mode 600) and applied immediately |
| **Context mode** | Full (all analytics) or Slim (fewer tokens, faster) |
| **Report window** | Weeks of history for coaching reports (4 / 8 / 12, default 8) — also drives the automatic 7-day report |
| **Send your name to the AI** | When off, the coach sees "the athlete" instead of your display name |
| **Send body data to the AI** | When off, weight / body-fat / height / BMI are excluded from the AI context |
| **Manage memories** | List and delete individual coach memories; set the automatic storage limit (default 200, oldest pruned) |
| **Monthly token budget** | Optional input+output token budget; warnings at 80% (yellow) and 100% (red) in the panel and at chat start — informational, never blocks |
| **Token counter** | Cumulative input / output / cache-read tokens with cache hit % |
| **Reset token counter** | Zero out the cumulative counters |

#### Developer

Everything here operates on the local per-profile data — nothing on Hevy or Google Fit is ever touched.

| Option | Description |
|---|---|
| **Export data** | JSON dumps to `~/.local/share/lifter/profiles/{slug}/exports/` — coach insights, goals + token usage, body measurements, or a full database dump (all tables) |
| **Import data** | Restore a previous export: pick a file from `exports/` or type a path, review the preview (rows per table), confirm. Replaces the dumped tables atomically — any error rolls everything back |
| **Preview AI context** | Writes the exact `<training_data>` block the chat sends to the AI provider to a `.md` file — fully local, no API call. Respects the privacy toggles |
| **Database info** | Row counts per table, DB path and size |
| **Debug logging** | on / off. Logs rotate automatically — the newest 14 daily files are kept |
| **Clear debug logs** | Deletes all `logs/debug-*.log` files at once |
| **Last sync status** | The panel shows the outcome (✓/✗ + detail) of the last Hevy and Google Fit sync attempts |
| **Reset data** | See below |

#### Reset data (under Developer)

| Option | What it clears |
|---|---|
| **Clear coach memories** | Deletes everything extracted from past chat sessions — the coach starts fresh with no prior context |
| **Clear all goals** | Removes all active goals; the wizard runs again on next launch |
| **Clear sync state** | Resets the sync timestamp so the next incremental sync re-downloads all workouts (existing local data is kept) |
| **Wipe everything** | Double-confirmed — deletes `hevy.db` and disconnects Google Fit (`fit_token.json`). Run **Sync → Full** afterwards to restore |

Tip: run **Export data → Full database dump** before a reset — you can bring everything back later with **Import data**.

You can also reset individual pieces manually (replace `{slug}` with your profile slug, e.g. `default`):

```bash
# Profile data lives under the XDG data dir (see "Where Lifter stores data")
PROFILE=~/.local/share/lifter/profiles/{slug}

# Delete the local database for a profile
rm "$PROFILE"/hevy.db

# Disconnect Google Fit for a profile (keeps DB data)
rm "$PROFILE"/fit_token.json

# Force the next sync to re-download everything
sqlite3 "$PROFILE"/hevy.db "UPDATE sync_state SET value='1970-01-01T00:00:00Z' WHERE key='last_sync';"

# Clear only coach memories
sqlite3 "$PROFILE"/hevy.db "DELETE FROM chat_memories;"
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

## Configuration reference

### `.env` (global, AI provider keys only)

Location: **`~/.config/lifter/.env`** (`$XDG_CONFIG_HOME/lifter/.env`). You rarely
need to edit it by hand — **Settings → AI Coach → API keys** manages it in-app.

```bash
# AI provider — pick one value for AI_PROVIDER, set the matching key.
# AI_PROVIDER and AI_MODEL are the defaults; each profile can override them
# in-app via Settings → AI Coach (the .env value becomes the fallback).
AI_PROVIDER=gemini        # gemini | claude | openrouter | groq | github | bedrock
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
GROQ_API_KEY=
GITHUB_TOKEN=
AI_MODEL=                 # optional — overrides the provider default (see setup section)

# Amazon Bedrock (only when AI_PROVIDER=bedrock) — use a bearer token OR AWS credentials
AWS_REGION=us-east-1
AWS_BEARER_TOKEN_BEDROCK= # Bedrock API key / bearer token (Claude models need no boto3)
AWS_ACCESS_KEY_ID=        # optional if using IAM role or aws configure
AWS_SECRET_ACCESS_KEY=
AWS_SESSION_TOKEN=        # optional, for temporary credentials

# Google Fit
GOOGLE_CREDENTIALS_FILE=fit_credentials.json   # path to downloaded OAuth JSON

# Optional directory overrides (shared across profiles)
# EXPORT_DIR=/home/you/lifter-exports   # where "Export data" writes (default: exports/ next to the profile DB)
# LOGS_DIR=/home/you/lifter-logs        # where debug logs go (default: ~/.local/state/lifter/logs)
```

### Per-profile config (`~/.local/share/lifter/profiles/{slug}/profile.json`)

Each profile stores its own settings that are set through the in-app menus:

| Key | Description |
|---|---|
| `name` | Display name shown in menus |
| `slug` | URL-safe identifier used as the directory name |
| `hevy_api_key` | API key for this profile's Hevy account |

Profile databases live at `~/.local/share/lifter/profiles/{slug}/hevy.db` and Google Fit tokens at `~/.local/share/lifter/profiles/{slug}/fit_token.json`.

All in-app preferences are stored in the `user_preferences` table in each profile's database, not in `.env`. Notable keys: `units`, `auto_sync`, `sync_stale_hours`, `goals_checkin_days`, `default_stats_weeks`, `report_weeks`, `ui_language`, `ai_provider` / `ai_model` (per-profile override of `.env`), `ai_send_name` / `ai_send_body` (privacy), `ai_tokens_month_budget`, `memories_max`, `debug_logging`.

---

## Debug logs

Lifter can write structured logs to `~/.local/state/lifter/logs/debug-YYYY-MM-DD.log` (one file per day). This is useful for diagnosing sync failures, AI errors, and profile events without having to instrument the code manually.

### Enabling

**Settings → Developer → Debug logging → on**

The setting takes effect immediately — no restart needed. The log path is printed to the terminal when you enable it.

### Rotation

Logs rotate by file count: at every startup the newest **14** daily files are kept and older ones are deleted (count-based, so sporadic usage still keeps your last 14 days of activity). **Settings → Developer → Clear debug logs** wipes them all at once.

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
| `[EXPORT]` | Data exports (kind, row count) and AI-context previews written |
| `[IMPORT]` | Data imports (kind, row count, source file) |
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

**Global bootstrap language** (shown before a profile is selected): set `DEFAULT_LANGUAGE=en` in `~/.config/lifter/.env`. This controls the language of the profile-selector screen.

### Supported languages

| Code | Name |
|------|------|
| `en` | English |
| `pt_BR` | Português (Brasil) |

Want to add a language? See [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-translation).

---

## License

Licensed under the AGPL-3.0 License — see [LICENSE](LICENSE).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, project architecture, the database schema, running tests, branch naming, commit format, CI checks, adding a translation, and the PR template.
