# Quota Dashboard

Static quota dashboard powered by `scanner.py` + `index.html`.

## What it does
- Runs `openclaw status --usage --json`.
- Normalizes and writes `data.json` atomically.
- Maintains bounded, deduped `history.json` for trends.
- Writes `summary.json` with health/error info for debugging.
- Optionally commits/pushes only when data/history changed meaningfully.

## Local run

### Linux / WSL
```bash
python scanner.py --no-git
```

### Windows (PowerShell)
```powershell
python .\scanner.py --no-git
```

The scanner resolves OpenClaw differently by platform:
- Windows: `cmd.exe /c openclaw.cmd status --usage --json`
- Linux/WSL: `openclaw status --usage --json`

## Flags and env
- `--dry-run`: parse/process only, no file writes and no git.
- `--no-git`: write JSON files but skip git add/commit/push.
- `--history-interval-min`: sample interval floor (default `10`, or env `HISTORY_INTERVAL_MIN`).
- `--history-max-samples`: max samples per model key (default `288`, or env `HISTORY_MAX_SAMPLES`).

## Scheduling
Recommended cadence: every 5–10 minutes.

### Cron
```cron
*/10 * * * * cd /path/to/quota-dashboard && /usr/bin/python3 scanner.py
```

### Windows Task Scheduler
- Trigger: every 10 minutes.
- Action: `python`
- Arguments: `scanner.py`
- Start in: repo folder.

## Hosting and caching (GitHub Pages)
- Publish the repo root as a static site.
- UI polls `data.json` and `history.json` every 30s with cache-busting query params.
- `localStorage` keeps last known good payload if a fetch fails.

## Optional sync endpoint config
Edit `config.json`:
```json
{
  "syncEndpoint": null,
  "syncMethod": "POST",
  "syncPayload": {},
  "refreshDelayMs": 3000
}
```

- If `syncEndpoint` is `null`, the button becomes **REFRESH** and just reloads data locally.
- If set, UI will call endpoint then refresh after `refreshDelayMs`.
- On CORS/network failure, UI shows a non-blocking error banner and remains usable.

## Troubleshooting usage jumps/resets
- Usage windows reset upstream; percentage can drop to 0 at reset boundaries.
- The scanner clamps percentages to `0..100` and rounds to 2 decimals.
- `resetAt` is normalized to epoch milliseconds and mirrored as `resetAtIso` for debug.
- If parser/execution fails, inspect `summary.json` for error details.
