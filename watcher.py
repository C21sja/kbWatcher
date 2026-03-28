import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configurations
API_URL = "https://api.jorato.com/tenancies?visibility=public&showAll=true&key=2gXoBtKvFMMgKJ1VBJ5G5pNr2GD"
APPLY_URL = "https://us-central1-kerebyudlejning-dk.cloudfunctions.net/createShowcasingRequest?key=2gXoBtKvFMMgKJ1VBJ5G5pNr2GD"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://kerebyudlejning.dk",
    "Referer": "https://kerebyudlejning.dk/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
}
SEEN_IDS_FILE = "seen_ids.json"

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
DISCORD_MENTION_USER_ID = os.environ.get("DISCORD_MENTION_USER_ID")

USER_NAME = os.environ.get("USER_NAME", "Test Testsen")
USER_EMAIL = os.environ.get("USER_EMAIL", "test@example.com")
USER_PHONE = os.environ.get("USER_PHONE", "12345678")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

try:
    RUN_COUNT = int(os.environ.get("WATCHER_RUNS", 440))
    SLEEP_SECONDS = int(os.environ.get("WATCHER_SLEEP_SECONDS", 45))
except ValueError:
    RUN_COUNT = 440
    SLEEP_SECONDS = 45


def get_next_workday_11am():
    now = datetime.now()
    days_ahead = 1
    if now.weekday() == 4:  # Friday
        days_ahead = 3
    elif now.weekday() == 5:  # Saturday
        days_ahead = 2

    next_day = now + timedelta(days=days_ahead)
    next_day_11 = next_day.replace(hour=11, minute=0, second=0, microsecond=0)

    danish_days = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
    danish_months = ["", "januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"]

    day_name = danish_days[next_day_11.weekday()]
    month_name = danish_months[next_day_11.month]

    booking_time_str = f"{day_name} den {next_day_11.day}. {month_name} {next_day_11.year}, kl. 11:00"
    
    # We construct a StartsAt using UTC ISO 8601 string. Assuming the system is roughly local, 
    # we just format it. The timezone info doesn't have to be perfect for the lead to register,
    # but let's make it explicitly Z (UTC) for safety.
    utc_starts_at = next_day_11.astimezone(timezone.utc)
    starts_at_str = utc_starts_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return starts_at_str, booking_time_str


def load_seen_states():
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading seen states: {e}")
            return {}
    return {}


def save_seen_states(states):
    try:
        with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(states, f, indent=2)
    except Exception as e:
            print(f"Error saving seen states: {e}")


def post_discord_payload(payload):
    if not WEBHOOK_URL:
        print("Webhook URL not configured.")
        return False
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": HEADERS["User-Agent"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status in [200, 204]
    except Exception as e:
        print(f"Failed to send Discord payload: {e}")
        return False


def post_discord_error(error_msg):
    if not WEBHOOK_URL:
        return False
    mention = build_discord_mention()
    payload = {
        "content" : f"{mention} :warning: **Watcher Error / Failsafe Triggered** :warning:\n```\n{error_msg}\n```"
    }
    if DISCORD_MENTION_USER_ID:
        payload["allowed_mentions"] = {"users": [DISCORD_MENTION_USER_ID]}
    return post_discord_payload(payload)


def build_discord_mention():
    if DISCORD_MENTION_USER_ID:
        return f"<@{DISCORD_MENTION_USER_ID}>"
    return "@everyone"


def check_criteria(apt):
    """
    Returns (True/False, reason_string)
    """
    if apt.get("state") != "Available":
        return False, "Not Available"
    
    if apt.get("classification") != "Residential":
        return False, "Not Residential"

    rent = 0
    size = 0
    try:
        rent = float(apt.get("monthlyRent", {}).get("value", 0))
        size = float(apt.get("size", {}).get("value", 0))
    except Exception:
        pass

    if rent >= 14000:
        return False, f"Rent too high ({rent})"
    
    if size <= 50:
        return False, f"Size too small ({size})"

    zip_code = apt.get("address", {}).get("zipCode", "")
    try:
        zip_int = int(zip_code)
    except ValueError:
        return False, f"Invalid zip ({zip_code})"

    valid_zip = (1000 <= zip_int <= 1499) or (1500 <= zip_int <= 1799) or (1800 <= zip_int <= 2000) or zip_int in [2100, 2200]
    
    if not valid_zip:
        return False, f"Wrong location ({zip_int})"
    
    return True, "Matches all criteria"


def attempt_application(apt):
    """
    Sends the POST request to book a viewing for next workday at 11:00.
    """
    starts_at_str, booking_time_str = get_next_workday_11am()
    
    tenancy_id = apt.get("id") # The UUID in the items array is the tenancy ID used in the application
    address = apt.get("address", {})
    street = address.get("street", "Unknown")
    zip_code = address.get("zipCode", "")
    city = address.get("city", "")
    full_address = f"{street}, {zip_code} {city}"

    # Build the URL based on title string formatting commonly used
    # But since it's just a payload field, we can construct a dummy or base it on data we have
    url_slug = "https://kerebyudlejning.dk/ledige-boliger/"

    note_text = os.environ.get("USER_MESSAGE", "")

    message_text = f"Jeg vil gerne komme til en fremvisning af nedenstående bolig på datoen {booking_time_str.split(', kl')[0]}. Disse tider passer mig: 11:00"

    payload = {
        "name": USER_NAME,
        "phoneNumber": USER_PHONE,
        "phoneExtension": "45",
        "email": USER_EMAIL,
        "startsAt": starts_at_str,
        "note": note_text,
        "communicationLanguage": "danish",
        "tenancyId": tenancy_id,
        "booking_time": booking_time_str,
        "message": message_text,
        "subject": full_address,
        "address": full_address,
        "url": url_slug,
        "commercial": False,
        "parking": False,
        "screeningAnswers": []
    }

    if DRY_RUN:
        print(f"[DRY RUN] Simulating application to {tenancy_id} for {booking_time_str}")
        print(f"[DRY RUN] Payload that would be sent to Kereby:\n{json.dumps(payload, indent=2)}")
        return True, f"{booking_time_str} (DRY RUN)"

    req = urllib.request.Request(
        APPLY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 200:
                print(f"Successfully applied to {tenancy_id} for {booking_time_str}")
                return True, booking_time_str
            else:
                return False, f"HTTP {response.status}"
    except Exception as e:
        print(f"Application exception for {tenancy_id}: {e}")
        return False, str(e)


def process_listing(apt, seen_states, is_first_run):
    apt_id = apt.get("id")
    if not apt_id:
        return

    status = apt.get("state", "Unknown")
    
    # If we've already seen this ID with this exact status, skip
    # (Checking exact status means if it changes from Reserved -> Available, we will catch it!)
    if seen_states.get(apt_id) == status:
        return
    
    # New or updated listing!
    previous_status = seen_states.get(apt_id)

    # We do NOT apply or post to discord if it's the very first run ever, 
    # to avoid spamming 70+ discord messages and applications when creating the initial DB.
    if is_first_run:
        seen_states[apt_id] = status
        return

    # Evaluate criteria
    matches, reason = check_criteria(apt)

    applied_status = "Not Applied"
    booking_time = "N/A"
    app_success = False

    # Apply if criteria matched and it's actually newly available
    if matches and status == "Available":
        print(f"Match found! Applying to {apt_id}...")
        app_success, apply_result = attempt_application(apt)
        if app_success:
            applied_status = "Successfully Applied!"
            booking_time = apply_result
        else:
            applied_status = f"Failed to apply: {apply_result}"
    elif status == "Available":
         applied_status = f"Skipped: {reason}"
    else:
         applied_status = f"Skipped: Status is '{status}'"

    # Send Discord notification
    address = apt.get("address", {})
    street = address.get("street", "Unknown")
    rent_val = apt.get("monthlyRent", {}).get("value", "Unknown")
    size_val = apt.get("size", {}).get("value", "Unknown")
    
    # Title logic
    title_prefix = "New Listing" if previous_status is None else "Status Update"
    embed_color = 3066993 if app_success else (16753920 if matches else 10070709)

    mention = build_discord_mention() if app_success else ""
    content = f"{mention} :rotating_light: **{title_prefix}: {street}** :rotating_light:"
    
    message = {
        "content" : content.strip(),
        "embeds": [
            {
                "title": f"[{status}] {apt.get('title', 'Unknown Title')}",
                "color": embed_color,
                "fields": [
                    {"name": "Status", "value": status, "inline": True},
                    {"name": "Rent", "value": f"{rent_val} kr/mo", "inline": True},
                    {"name": "Size", "value": f"{size_val} m2", "inline": True},
                    {"name": "Address", "value": f"{street}, {address.get('zipCode')} {address.get('city')}", "inline": False},
                    {"name": "Application Status", "value": applied_status, "inline": False},
                    {"name": "Booking Time Requested", "value": booking_time, "inline": False},
                ],
                "footer": {"text": f"Kereby Watcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
            }
        ]
    }

    if app_success and DISCORD_MENTION_USER_ID:
         message["allowed_mentions"] = {"users": [DISCORD_MENTION_USER_ID]}

    post_discord_payload(message)
    print(f"Processed {apt_id}: {applied_status}")

    seen_states[apt_id] = status
    save_seen_states(seen_states)


def fetch_apartments():
    req = urllib.request.Request(API_URL, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status != 200:
                print(f"API HTTP issue: {response.status}")
                return []
            raw_data = response.read().decode("utf-8")
            data = json.loads(raw_data)
            return data.get("items", [])
    except Exception as e:
        print(f"Error fetching API: {e}")
        try:
            post_discord_error(f"API Fetch Error: {e}")
        except:
            pass
        return []


def main():
    if "--test" in sys.argv:
        print("--- RUNNING INSTANT TEST MODE ---")
        global DRY_RUN
        DRY_RUN = True
        items = fetch_apartments()
        if items:
            apt = items[0]
            # Mock the apartment data so it perfectly passes all criteria checks
            apt["state"] = "Available"
            apt["classification"] = "Residential"
            apt["monthlyRent"] = {"value": 5000}
            apt["size"] = {"value": 100}
            if "address" not in apt:
                apt["address"] = {}
            apt["address"]["zipCode"] = "1000"
            apt["address"]["street"] = "Test Street 1"
            process_listing(apt, {}, False)
        print("--- TEST COMPLETE ---")
        return

    try:
        seen_states = load_seen_states()
        is_first_run = len(seen_states) == 0

        if is_first_run:
            print("First run detected. Caching existing properties without screaming in Discord.")

        for run_num in range(1, RUN_COUNT + 1):
            print(f"--- Run {run_num}/{RUN_COUNT} - {datetime.now().strftime('%H:%M:%S')} ---")
            items = fetch_apartments()
            print(f"Fetched {len(items)} properties.")

            for item in items:
                try:
                    process_listing(item, seen_states, is_first_run)
                except Exception as e:
                    print(f"Error processing item: {e}")
                    try:
                        post_discord_error(f"Error processing listing {item.get('id', 'Unknown')}: {e}")
                    except:
                        pass
            
            # After the first pass, it is no longer the first run ever
            if is_first_run:
                is_first_run = False
                
            if run_num < RUN_COUNT:
                time.sleep(SLEEP_SECONDS)
                
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"Fatal error in main loop: {err}")
        try:
            post_discord_error(f"Fatal Exception in Watcher:\n{err[-1500:]}")
        except:
            pass
        raise

if __name__ == "__main__":
    main()
