# Security Policy

## Supported versions

Only the latest commit on `main` is actively maintained.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately by emailing **joao@golister.app** with:

- A description of the vulnerability
- Steps to reproduce
- The potential impact
- Any suggested fix (optional)

You will receive an acknowledgement within 48 hours and a patch within 7 days for confirmed issues.

## Security model

lifter is a local-first personal tool. The threat model is:

| Asset | Where stored | Risk |
|---|---|---|
| Hevy API key | `.env` (local) | Anyone with read access to your machine |
| Gemini / Claude / OpenRouter / Groq / GitHub API key | `.env` (local) | Same |
| AWS credentials (Bedrock) | `.env` (local) | Same |
| Google OAuth token | `profiles/{slug}/fit_token.json` (local) | Same — written with 0o600 permissions |
| Google OAuth client secret | `fit_credentials.json` (local) | Same |
| Workout / health data | `profiles/{slug}/hevy.db` (local SQLite) | Same |
| Profile configuration | `profiles.json`, `profiles/{slug}/profile.json` (local) | Same |

None of this data is transmitted to any server except the respective APIs (Hevy, Google Fit, and whichever AI provider is configured: Google AI, Anthropic, OpenRouter, Groq, GitHub Models, or AWS Bedrock).

## Best practices for users

- Keep `.env`, `fit_credentials.json`, `profiles/`, `profiles.json`, and `*.db` out of version control (`.gitignore` covers this)
- Run `chmod 600 .env` after every fresh clone
- Rotate API keys if you suspect they were exposed
- Do not share your project directory with untrusted users

## Known limitations

- The app is designed for a single user on a personal machine — it is not hardened for multi-user or server deployment
- AI prompt content (your training data and goals) is sent to the configured AI provider; review their privacy policy
