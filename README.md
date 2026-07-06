# Kereby Apartment Watcher

An automated, long-running Python script that monitors the Kereby Udlejning backend API for newly available rental properties. When a property matching your precise criteria (rent, size, specific ZIP codes) becomes available, the script automatically sends a POST request to book a viewing for the next workday at 11:00 AM and posts a rich notification to your designated Discord channel.

## Features
- **Adaptive, Time-Aware Monitoring:** Queries the underlying property API on a Copenhagen-time schedule that matches when Kereby actually publishes. Listings appear almost exclusively on weekday business hours (residential observed only 10:00–15:00 CPH, peak ~13:00), and freed flats flip `Available → Reserved` within ~90s–5min. So the watcher polls hardest during that peak window and backs off to 300s overnight/weekends — catching the short openings faster while making far fewer total requests than a flat 45s cadence.
- **Cache-Aware Polling:** The feed is served through a CloudFront CDN with a ~30-second edge cache, so polling faster than that just re-downloads identical bytes. During the responsive tiers the watcher reads the response's `Age` header and schedules the next poll to land *just after* the next cache refresh (`seconds_to_refresh ≈ TTL − Age`). In practice it catches each new cache generation within ~1–2 seconds using only ~1 request per 30s cycle — lower latency **and** fewer requests than brute-force fast polling.
- **Auto-Application:** Skips the manual UI booking forms and submits your contact details directly to the Google Cloud backend as soon as a property is available.
- **Smart Filtering:** Only applies to apartments that match your specific requirements (e.g., specific neighborhoods, max rent, minimum size).
- **Discord Integration:** Sends rich embeds to a Discord Webhook, notifying you of new listings and application attempt statuses.
- **GitHub Actions Ready:** Includes a `watcher.yml` workflow to run continuously in the cloud for free, utilizing long-running jobs to maximize uptime.

## Setup Requirements

### Environment Variables / Repository Secrets
To run this script (locally or in GitHub Actions), the following environment variables need to be configured:

- `DISCORD_WEBHOOK_URL`: The webhook URL for the Discord channel where alerts will be sent.
- `DISCORD_MENTION_USER_ID`: (Optional) Your Discord User ID to ping you directly upon a successful application.
- `USER_NAME`: Your full name to be submitted in the viewing request.
- `USER_EMAIL`: Your email address for contact.
- `USER_PHONE`: Your phone number (+45 is added automatically).
- `WATCHER_MAX_RUNTIME_SECONDS`: How long a single run stays alive before exiting cleanly (Default: 18000 / 5h; the GitHub workflow overrides this to 4200 / 70min).

### Polling configuration (all optional)
Polling is **adaptive** by default, keyed to Copenhagen local time (DST-aware, zero dependencies). Each poll is classified into an activity tier:

| Tier | When (Copenhagen local) | Behavior |
| --- | --- | --- |
| `HOT` | Weekdays 10:00–15:00 (peak ~13:00) | **Cache-synced** (≈1 poll per 30s CDN cycle, ~1–2s latency); falls back to 20s if `Age` is missing |
| `WARM` | Weekdays 08:00–10:00 & 15:00–17:00 | **Cache-synced**; falls back to 45s if `Age` is missing |
| `COOL` | Weekdays 07:00–08:00 & 17:00–22:00; weekends 09:00–18:00 | Fixed 120s |
| `COLD` | Overnight & the rest of the weekend | Fixed 300s |

**Tier intervals:**
- `WATCHER_POLL_HOT_SECONDS` / `WATCHER_POLL_WARM_SECONDS` / `WATCHER_POLL_COOL_SECONDS` / `WATCHER_POLL_COLD_SECONDS`: Override any tier's interval (floored at 10s). For `HOT`/`WARM` this is only the fallback used when the CDN `Age` header is unavailable.

**Cache-aware polling** (applies to `HOT`/`WARM`):
- `WATCHER_CDN_CACHE_TTL`: Assumed CloudFront edge-cache TTL in seconds (Default: 30).
- `WATCHER_CACHE_SYNC_MARGIN`: Seconds to poll *after* the expected refresh, so the new generation is present (Default: 2).
- `WATCHER_CACHE_SYNC_MIN`: Floor so we never busy-loop right at expiry (Default: 5).

**Global switches:**
- `WATCHER_ADAPTIVE_POLLING`: Set to `false` to disable both the schedule and cache-sync, polling at a fixed cadence instead.
- `WATCHER_SLEEP_SECONDS`: The fixed interval used only when adaptive polling is disabled (Default: 45).

## Running Locally

1. Clone the repository.
2. (Optional) Create a virtual environment.
3. Set the required environment variables in your terminal.
4. Run the script:
   ```bash
   python watcher.py
   ```

*Note: On the very first run, the script will cache all current properties in `seen_ids.json` without sending Discord notifications to prevent spamming your channel.*

## Running via GitHub Actions

The repository includes a `.github/workflows/watcher.yml` file designed to run the watcher on a schedule.

1. Navigate to your GitHub Repository Settings > Secrets and variables > Actions.
2. Add all the required environment variables listed above as **Repository Secrets**.
3. The workflow triggers **hourly** (`0 * * * *`). Each job runs ~70 minutes — longer than the 60-minute trigger interval — so a fresh run is always queued and, serialized by the `concurrency` group, takes over the instant the previous job exits. This gives **continuous coverage with no blind gaps** and tolerates GitHub's occasional late or dropped scheduled runs, since the active job keeps polling until the next one starts.