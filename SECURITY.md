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
| Hevy API key | `~/.local/share/lifter/profiles/{slug}/profile.json` (local) | Anyone with read access to your machine |
| Gemini / Claude / OpenRouter / Groq / GitHub API key | `~/.config/lifter/.env` (local, mode 600) | Same |
| AWS credentials (Bedrock) | `~/.config/lifter/.env` (local, mode 600) | Same |
| Google OAuth token | `~/.local/share/lifter/profiles/{slug}/fit_token.json` (local) | Same — written with 0o600 permissions |
| Google OAuth client secret | `~/.config/lifter/fit_credentials.json` (local) | Same |
| Workout / health data | `~/.local/share/lifter/profiles/{slug}/hevy.db` (local SQLite) | Same |
| Profile configuration | `~/.local/share/lifter/profiles.json` and `profiles/{slug}/profile.json` (local) | Same |

None of this data is transmitted to any server except the respective APIs (Hevy, Google Fit, and whichever AI provider is configured: Google AI, Anthropic, OpenRouter, Groq, GitHub Models, or AWS Bedrock).

## Best practices for users

- All secrets and data live outside the repository (in `~/.config/lifter/` and `~/.local/share/lifter/`), so a clone never contains them; the `.gitignore` additionally blocks legacy-layout files from being committed
- Rotate API keys if you suspect they were exposed
- Do not share your home directory (`~/.config/lifter/`, `~/.local/share/lifter/`) with untrusted users

## Known limitations

- The app is designed for a single user on a personal machine — it is not hardened for multi-user or server deployment
- AI prompt content (your training data and goals) is sent to the configured AI provider; review their privacy policy
