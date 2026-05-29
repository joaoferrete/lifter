# hevy-proxy

Personal Hevy workout client with analytics, goal tracking, Google Fit integration, and an AI coach that remembers your conversations.

---

## What it does

| Feature | Details |
|---|---|
| **Sync** | Fetches your full Hevy workout history locally. Every sync shows a report with new workouts, PRs set, training streak, and volume vs last week. |
| **Analytics** | Volume per muscle group, exercise progression (e1RM), personal records, plateau detection. |
| **Goals** | Set lift targets, frequency goals, body weight / fat / volume targets. Tracks progress automatically after every sync. |
| **AI Coach** | One-shot report: strengths, weaknesses, recommendations, and a complete routine tailored to your goals. |
| **AI Chat** | Interactive coach that knows your full history, can push routines to Hevy, and can update your goals — all with your approval. |
| **Memory** | After every chat the AI extracts key insights (injuries, preferences, feedback) and saves them. Future sessions start with that context already loaded. |
| **Google Fit** | Syncs sleep, steps, calories, and resting HR. Recovery score shown in the header. Coach uses recovery data when making suggestions. |
| **Multi-model** | Works with Gemini (default) or Claude — swap with one env variable. |

---

## Requirements

- Python 3.11+
- [Hevy Pro](https://hevy.com) subscription (API access is Pro-only)
- One of: Gemini API key **or** Anthropic API key
- (Optional) Google account with Fitness data

---

## Setup

### 1. Install dependencies

```bash
cd hevy-proxy
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in your keys (see sections below).

### 3. Get your Hevy API key

1. Log in at [hevy.com](https://hevy.com)
2. Go to **Profile → Settings → Developer**
3. Copy your API key
4. Add to `.env`:

```
HEVY_API_KEY=your-key-here
```

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

### 5. Run the initial sync

```bash
python3 cli.py
```

The interactive menu opens. Select **Sync new workouts → Full** to download your entire Hevy history.

---

## Google Fit setup (optional)

Adds sleep, steps, calories, and heart rate data to your analytics and AI context.

### Step 1 — Create OAuth credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. **APIs & Services → Library** → search for **Fitness API** → Enable it
4. **APIs & Services → OAuth consent screen**
   - User type: External
   - Fill in app name (anything, e.g. "hevy-proxy")
   - Add your Gmail as a **Test user** → Save
5. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything
   - Click Create
6. **Download JSON** → rename the file to `fit_credentials.json` → place it in the `hevy-proxy/` folder

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
  Dashboard & stats
  Exercise progression
  Personal records
  My goals
  ─────────────────────
  Google Fit  (sleep, steps, HR)
  ─────────────────────
  AI coaching report
  Chat with coach
  ─────────────────────
  Exit
```

### Sync new workouts

Downloads workout data from Hevy. After every sync:
- **Workout cards** show each new session with exercises, weights, and **★ PR** badges
- **Training streak** with fire emojis
- **Volume bar chart** vs last week with % change per muscle group
- **Goal progress** panel with bars for all active goals

Choose **Incremental** (default, only fetches changes) or **Full** (re-downloads everything).

### Dashboard & stats

Full analytics for a selectable time period (4 / 8 / 12 / 24 weeks):
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

**First run**: the wizard runs automatically to set your goals.  
**Weekly check-in**: every 7 days the app asks if your goals are still the same.  
**Progress bars**: shown after every sync (green ≥80%, yellow ≥50%, red <50%, ★ when achieved).

### Google Fit

- **Sync health data**: pulls sleep sessions, daily steps, calories, and heart rate
- **Recovery dashboard**: sleep averages, nightly consistency, resting HR, active minutes
- **Recovery score (0–100)**: composite of recent sleep quality and resting HR trend
  - 80–100: Excellent | 65–79: Good | 45–64: Fair | <45: Poor
- **Connect / re-authenticate**: runs the OAuth browser flow
- **Disconnect**: removes the token (local data stays in the DB)

### AI coaching report

Analyses your training data against your goals and generates:
- **Strengths**: what's working
- **Weaknesses**: imbalances, underworked muscles, plateaus
- **Recommendations**: 3–5 actionable tips for the next weeks
- **Next focus**: the single most important thing to address
- **Suggested routine**: a complete workout with exercises, sets, reps, and weights matched to your current strength level — ready to push to Hevy

Select the number of weeks to analyse (4 / 8 / 12 / 16). After the report, the app asks if you want to push the routine directly to your Hevy app.

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
| Add a new goal | "Add a goal to deadlift 180kg" |
| Update a goal | "Change my bench goal to 130kg" |
| Remove a goal | "Remove my weight loss goal" |

All goal changes and routine pushes require your explicit confirmation before anything is saved.

**After the conversation ends**, the AI analyses the full transcript and extracts memorable facts (injuries mentioned, exercise preferences, feedback on suggestions, lifestyle context). These are saved and automatically included in all future sessions.

---

## Architecture

```
hevy-proxy/
├── hevy/
│   ├── client.py        API wrapper (all Hevy endpoints + payload sanitization)
│   └── sync.py          Full + incremental sync via /v1/workouts/events
├── db/
│   ├── store.py         SQLite schema + upsert helpers
│   ├── goals.py         Goal CRUD + progress computation
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
│   ├── provider.py      Unified ChatSession abstraction (Gemini + Claude)
│   └── coach.py         Coaching report, chat loop, goal tools, memory extraction
├── cli.py               Interactive menu (questionary + Rich)
├── config.py            .env loader
├── hevy.db              Local SQLite database (created on first sync)
├── fit_credentials.json Google OAuth credentials (you create this)
└── fit_token.json       Google OAuth token (created automatically)
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
| `user_goals` | Active and achieved training goals |
| `user_preferences` | Name, last goals check-in, other settings |
| `chat_memories` | Insights extracted from past conversations |
| `sync_state` | Last sync timestamp and other state keys |

---

## Configuration reference

All settings live in `.env`:

```bash
# Hevy
HEVY_API_KEY=             # from hevy.com/settings?developer

# AI provider — choose one
AI_PROVIDER=gemini        # "gemini" or "claude"
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
AI_MODEL=                 # optional override (defaults: gemini-2.5-pro / claude-opus-4-8)

# Google Fit
GOOGLE_CREDENTIALS_FILE=fit_credentials.json   # path to downloaded OAuth JSON

# Database
DB_PATH=hevy.db           # path to local SQLite file
```
