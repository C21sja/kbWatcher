# Kereby Apartment Watcher

An automated, long-running Python script that monitors the Kereby Udlejning backend API for newly available rental properties. When a property matching your precise criteria (rent, size, specific ZIP codes) becomes available, the script automatically sends a POST request to book a viewing for the next workday at 11:00 AM and posts a rich notification to your designated Discord channel.

## Features
- **Continuous Monitoring:** Bypasses browser latency by directly querying the underlying property API every 45 seconds.
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
- `WATCHER_RUNS`: The number of times the script should poll before exiting (Default: 440).
- `WATCHER_SLEEP_SECONDS`: Seconds to sleep between polls (Default: 45).

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
3. The workflow is configured to trigger every 6 hours via GitHub cron (`0 */6 * * *`). It will securely loop for roughly 5.5 hours per job, providing near-continuous 45-second polling without exceeding maximum workflow execution times.