# hevy-proxy

Personal Hevy workout client with analytics and AI coaching. Syncs your workout history locally, tracks progression and personal records, and lets you chat with an AI coach powered by Gemini.

> Requires a [Hevy Pro](https://hevy.com) subscription (API access is Pro-only).

---

## What it does

- **Syncs** all your workouts, exercise templates, and body measurements to a local SQLite database
- **Incremental updates** — after the first sync, only fetches what changed using the events API
- **Sync summary** — every sync shows a visual report of new workouts, PRs set, volume vs last week, and your training streak
- **Analytics** — volume by muscle group, progression curves, personal records, plateau detection
- **AI coach** — Gemini analyzes your training data and generates insights + a ready-to-use routine
- **AI chat** — interactive coach that knows your full training history and answers any question

---

## Setup

**1. Clone and install dependencies:**

```bash
cd hevy-proxy
pip install -r requirements.txt
```

**2. Configure your API keys:**

```bash
cp .env.example .env
```

Edit `.env`:

```
HEVY_API_KEY=your-hevy-api-key       # from hevy.com/settings?developer
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-2.5-pro          # optional, this is the default
```

**3. Run the initial sync:**

```bash
python3 cli.py sync --full
```

---

## Commands

### `sync` — fetch new data

```bash
python3 cli.py sync           # incremental: only fetches workouts added since last sync
python3 cli.py sync --full    # full re-sync of everything
```

After every sync a summary is printed showing:
- New workouts with exercises and any PRs set
- Your current training streak
- Volume this week vs last week per muscle group

---

### `stats` — training analytics dashboard

```bash
python3 cli.py stats             # last 8 weeks (default)
python3 cli.py stats --weeks 12  # last 12 weeks
```

Shows:
- Workout frequency, average duration, rest day patterns
- Weekly tonnage per muscle group with inline bar chart
- Body measurement trends
- Personal records set in the last 30 days
- Plateau warnings (exercises with no progression)

---

### `progress` — exercise progression

```bash
python3 cli.py progress                   # list top gainers (biggest e1RM improvement)
python3 cli.py progress "bench press"     # progression history for a specific exercise
python3 cli.py progress "squat" --weeks 24
```

Uses the Epley formula (`weight × (1 + reps/30)`) to compute estimated 1RM per session so you can compare sets with different rep ranges.

---

### `records` — all-time personal records

```bash
python3 cli.py records
```

Shows the best set ever recorded for every exercise, ranked by estimated 1RM.

---

### `coach` — AI coaching report

```bash
python3 cli.py coach                     # analyse last 8 weeks
python3 cli.py coach --weeks 12          # use 12 weeks of history
python3 cli.py coach --push              # push the suggested routine to Hevy
python3 cli.py coach --output plan.json  # save full JSON to file
```

Gemini analyzes your training data and returns:
- **Strengths** — what's working
- **Weaknesses** — imbalances or underworked muscles
- **Recommendations** — actionable changes for the next weeks
- **Suggested routine** — a complete workout with sets, reps, and weights based on your current level

The suggested routine can be pushed directly to your Hevy app with `--push`, where it will appear in your routines list.

---

### `chat` — interactive AI coach

```bash
python3 cli.py chat             # chat with context from last 8 weeks
python3 cli.py chat --weeks 16  # use more history
```

Opens an interactive session where you can ask anything:

```
You: How's my chest progress looking?
Coach: Your chest volume has been consistent at ~2,400 kg/week...

You: What should I do tomorrow given I trained legs today?
Coach: Given your training split, tomorrow would be a good day for...

You: quit
```

Type `quit`, `exit`, or press Ctrl+C to end the session.

---

## Architecture

```
hevy-proxy/
├── hevy/
│   ├── client.py       API wrapper (all endpoints + pagination)
│   └── sync.py         Full and incremental sync logic
├── db/
│   └── store.py        SQLite schema and upsert helpers
├── analytics/
│   ├── volume.py       Weekly tonnage per muscle group
│   ├── progression.py  e1RM progression and plateau detection
│   ├── frequency.py    Workout cadence and session duration
│   └── records.py      Personal records and body measurement trends
├── ai/
│   └── coach.py        Gemini integration: coaching report + chat session
├── cli.py              Typer CLI (sync, stats, progress, records, coach, chat)
├── config.py           Reads .env settings
└── hevy.db             Local SQLite database (created on first sync)
```

---

## Notes

- The local database (`hevy.db`) is the source of truth for all analytics. All commands read from it — no API calls except during `sync`.
- The AI coach only suggests exercises you've already logged, so routine IDs are always valid.
- Body measurements are optional. If you don't log them in Hevy, those sections are simply omitted.
- The `--push` flag on `coach` calls `POST /v1/routines` to create the routine in your Hevy account. You can review it in the app before using it.
