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
APPLICATION_LOG_FILE = "application_log.json"

KNOWN_PAYLOAD_FIELDS = {
    "name", "phoneNumber", "phoneExtension", "email", "startsAt", "note",
    "communicationLanguage", "tenancyId", "booking_time", "message",
    "subject", "address", "url", "commercial", "parking", "screeningAnswers",
}

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


def load_application_log():
    if os.path.exists(APPLICATION_LOG_FILE):
        try:
            with open(APPLICATION_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def append_application_log(entry):
    log = load_application_log()
    log.append(entry)
    try:
        with open(APPLICATION_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing application log: {e}")


def inspect_listing(apt):
    """Inspect a listing for fields that suggest extra requirements we might miss.
    Returns a dict with screening_questions found, unknown keys, and warnings."""
    warnings = []
    screening_questions = apt.get("screeningQuestions") or apt.get("screening_questions") or []
    if screening_questions:
        warnings.append(f"Listing has {len(screening_questions)} screening question(s) that we send empty")

    known_listing_keys = {
        "id", "state", "classification", "monthlyRent", "size", "address",
        "title", "description", "descriptions", "images", "floorPlans",
        "availableFrom", "deposit", "prepaidRent", "rooms", "floor",
        "petsAllowed", "balcony", "elevator", "type", "types", "created",
        "updated", "area", "heatingType", "energyLabel", "utilities",
        "screeningQuestions", "screening_questions", "customFields",
        "requirements", "applicationRequirements", "documents", "tags",
        "features", "coordinates", "lat", "lng", "latitude", "longitude",
        "additionalDetails", "appliances", "campaign", "currentCaseId",
        "expenses", "kind", "locations", "propertyFacilities", "propertyId",
        "prospectus", "publishedAt", "responsibleEmployee",
        "tenancyFacilities", "terms", "virtualTour", "visibility",
    }
    unexpected_keys = set(apt.keys()) - known_listing_keys
    if unexpected_keys:
        warnings.append(f"Listing has unknown keys we may need to handle: {sorted(unexpected_keys)}")

    custom_fields = apt.get("customFields") or apt.get("applicationRequirements") or []
    if custom_fields:
        warnings.append(f"Listing has custom/application fields: {json.dumps(custom_fields, ensure_ascii=False)[:300]}")

    requirements = apt.get("requirements")
    if requirements:
        warnings.append(f"Listing has requirements field: {json.dumps(requirements, ensure_ascii=False)[:300]}")

    documents = apt.get("documents")
    if documents:
        warnings.append(f"Listing requires documents: {json.dumps(documents, ensure_ascii=False)[:200]}")

    return {
        "screening_questions": screening_questions,
        "unexpected_keys": sorted(unexpected_keys) if unexpected_keys else [],
        "warnings": warnings,
    }


def validate_pre_submit(apt, payload, inspection):
    """Returns (ok: bool, issues: list[str]).
    ok=False means we should NOT submit — critical field gap detected."""
    issues = []

    if not payload.get("tenancyId"):
        issues.append("CRITICAL: tenancyId is missing from payload")

    if not payload.get("name") or payload["name"] == "Test Testsen":
        issues.append("CRITICAL: name is missing or placeholder")

    if not payload.get("email") or payload["email"] == "test@example.com":
        issues.append("CRITICAL: email is missing or placeholder")

    if not payload.get("phoneNumber") or payload["phoneNumber"] == "12345678":
        issues.append("CRITICAL: phone is missing or placeholder")

    if inspection["screening_questions"]:
        sq = inspection["screening_questions"]
        if not payload.get("screeningAnswers"):
            issues.append(f"CRITICAL: {len(sq)} screening question(s) exist but screeningAnswers is empty")

    critical = any(i.startswith("CRITICAL") for i in issues)
    return (not critical), issues


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
    Validates payload against listing schema before sending and logs the full
    request/response cycle for audit.
    """
    starts_at_str, booking_time_str = get_next_workday_11am()
    
    tenancy_id = apt.get("id")
    address = apt.get("address", {})
    street = address.get("street", "Unknown")
    zip_code = address.get("zipCode", "")
    city = address.get("city", "")
    full_address = f"{street}, {zip_code} {city}"

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

    inspection = inspect_listing(apt)
    submit_ok, validation_issues = validate_pre_submit(apt, payload, inspection)

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "tenancy_id": tenancy_id,
        "address": full_address,
        "booking_time": booking_time_str,
        "inspection": inspection,
        "validation_issues": validation_issues,
        "payload_fields": sorted(payload.keys()),
        "listing_keys": sorted(apt.keys()),
        "dry_run": DRY_RUN,
        "submitted": False,
        "response_status": None,
        "response_body": None,
        "result": None,
    }

    if validation_issues:
        print(f"[VALIDATION] {tenancy_id}: {validation_issues}")

    if not submit_ok:
        log_entry["result"] = "BLOCKED_BY_VALIDATION"
        append_application_log(log_entry)
        return False, f"Blocked: {'; '.join(validation_issues)}"

    if DRY_RUN:
        print(f"[DRY RUN] Simulating application to {tenancy_id} for {booking_time_str}")
        print(f"[DRY RUN] Payload that would be sent to Kereby:\n{json.dumps(payload, indent=2)}")
        if inspection["warnings"]:
            print(f"[DRY RUN] Inspection warnings: {inspection['warnings']}")
        log_entry["result"] = "DRY_RUN"
        append_application_log(log_entry)
        return True, f"{booking_time_str} (DRY RUN)"

    req = urllib.request.Request(
        APPLY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            log_entry["response_status"] = response.status
            log_entry["response_body"] = response_body[:2000]
            log_entry["submitted"] = True

            response_data = None
            try:
                response_data = json.loads(response_body)
            except (json.JSONDecodeError, ValueError):
                pass

            if response.status == 200:
                log_entry["result"] = "SUCCESS"
                append_application_log(log_entry)

                if response_data and _response_has_issues(response_data):
                    issue_summary = f"API returned 200 but response suggests issues: {response_body[:500]}"
                    print(f"[RESPONSE WARNING] {tenancy_id}: {issue_summary}")
                    post_discord_warning(tenancy_id, full_address, inspection["warnings"], validation_issues, issue_summary)

                elif inspection["warnings"]:
                    post_discord_warning(tenancy_id, full_address, inspection["warnings"], validation_issues, None)

                print(f"Successfully applied to {tenancy_id} for {booking_time_str}")
                return True, booking_time_str
            else:
                log_entry["result"] = f"HTTP_{response.status}"
                append_application_log(log_entry)
                return False, f"HTTP {response.status}"
    except Exception as e:
        print(f"Application exception for {tenancy_id}: {e}")
        error_body = ""
        if hasattr(e, "read"):
            try:
                error_body = e.read().decode("utf-8", errors="replace")[:2000]
            except Exception:
                pass
        log_entry["response_status"] = getattr(e, "code", None)
        log_entry["response_body"] = error_body or str(e)
        log_entry["result"] = "EXCEPTION"
        log_entry["submitted"] = True
        append_application_log(log_entry)
        return False, str(e)


def _response_has_issues(data):
    """Heuristic check for error signals in a 200 response body."""
    if isinstance(data, dict):
        for key in ("error", "errors", "message", "validationErrors", "missing", "rejected"):
            val = data.get(key)
            if val and val not in ([], {}, "", None, False):
                return True
        if data.get("success") is False or data.get("ok") is False:
            return True
    return False


def post_discord_warning(tenancy_id, address, inspection_warnings, validation_issues, response_issue):
    """Alert on Discord when an application may be incomplete."""
    if not WEBHOOK_URL:
        return
    mention = build_discord_mention()
    lines = [f"{mention} :mag: **Application Quality Alert** — `{address}`"]
    if inspection_warnings:
        lines.append("**Listing inspection:**")
        for w in inspection_warnings:
            lines.append(f"  - {w}")
    if validation_issues:
        lines.append("**Validation issues:**")
        for v in validation_issues:
            lines.append(f"  - {v}")
    if response_issue:
        lines.append(f"**API response concern:** {response_issue}")
    lines.append(f"\n`tenancyId: {tenancy_id}`")

    payload = {"content": "\n".join(lines)}
    if DISCORD_MENTION_USER_ID:
        payload["allowed_mentions"] = {"users": [DISCORD_MENTION_USER_ID]}
    post_discord_payload(payload)


def process_listing(apt, seen_states, is_first_run):
    apt_id = apt.get("id")
    if not apt_id:
        return
    apt_id = str(apt_id)

    status = apt.get("state", "Unknown")
    
    # If we've already seen this ID with this exact status, skip
    # (Checking exact status means if it changes from Reserved -> Available, we will catch it!)
    if seen_states.get(apt_id) == status:
        return
    
    # Ignore parking spaces and 0 size properties entirely
    title = apt.get("title", "").lower()
    street = apt.get("address", {}).get("street", "").lower()
    try:
        size = float(apt.get("size", {}).get("value", 0))
    except Exception:
        size = 0.0

    if "p-plads" in title or "p-plads" in street or size <= 0.0:
        seen_states[apt_id] = status
        save_seen_states(seen_states)
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

    inspection = inspect_listing(apt)
    if inspection["warnings"]:
        print(f"[INSPECT] {apt_id}: {inspection['warnings']}")
    if inspection["unexpected_keys"]:
        print(f"[INSPECT] {apt_id} has unexpected listing keys: {inspection['unexpected_keys']}")

    applied_status = "Not Applied"
    app_success = False

    if matches and status == "Available":
        print(f"Match found! Alerting for {apt_id} (auto-apply disabled)...")
        applied_status = "Match found — apply manually!"
        app_success = True
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
                ] + ([{"name": ":warning: Inspection Warnings", "value": "\n".join(inspection["warnings"])[:1024], "inline": False}] if inspection["warnings"] else []),
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
