import asyncio
from datetime import datetime, timedelta, timezone
from tqdm.asyncio import tqdm
from cli import ask, yes_no, section, warn, green, yellow, red, print_table, print_summary, explain_create_error
from client import ApiError
from config import TRIAL_HOURS, TRIAL_MINUTES
from profiles import build_connexcs_profile, build_pbx_profile, build_connection_payload

def get_expiry_time():
    t = ask("Trial or Paid? (T/P): ").upper()
    if t == "T":
        return datetime.now(timezone.utc) + timedelta(hours=TRIAL_HOURS, minutes=TRIAL_MINUTES)
    if t == "P":
        days = int(ask("Paid expiry days: "))
        return datetime.now(timezone.utc) + timedelta(days=days)
    print("Invalid account type.")
    return get_expiry_time()

def collect_rows(profile_type, single):
    rows = []
    if profile_type == "pbx":
        domain = ask("PBX Domain: ")
        if single:
            email = ask("Email: ").lower()
            ext = ask("Extension: ")
            did = ask("DID: ")
            rows.append({"email": email, "extension": ext, "did": did, "domain": domain})
        else:
            print("\nPaste rows: email,extension,did")
            print("Empty line to finish.\n")
            while True:
                line = ask("", allow_empty=True)
                if not line:
                    break
                parts = [p.strip() for p in line.split(",")]
                if len(parts) != 3:
                    warn(f"Invalid: {line}")
                    continue
                rows.append({"email": parts[0].lower(), "extension": parts[1], "did": parts[2], "domain": domain})
        return rows
    if single:
        email = ask("Email: ").lower()
        caller_id = ask("Caller ID: ")
        rows.append({"email": email, "caller_id": caller_id})
    else:
        print("\nPaste rows: email,callerid")
        print("Empty line to finish.\n")
        while True:
            line = ask("", allow_empty=True)
            if not line:
                break
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 2:
                warn(f"Invalid: {line}")
                continue
            rows.append({"email": parts[0].lower(), "caller_id": parts[1]})
    return rows

async def provision_one(client, profile_type, row, expiry_time, send_welcome, rec, vm):
    email = row["email"]
    profile = build_pbx_profile(row["domain"], row["extension"], row["did"]) if profile_type == "pbx" else build_connexcs_profile(row["caller_id"])

    try:
        data = await client.request("POST", f"/Users/{client.owner_user_id}/DomainUsers",
                                    json={"Email": email, "UserType": "Internal", "Description": profile["description"],
                                          "JoinMailingList": True, "SendWelcomeEmail": send_welcome})
        uid = data["UserEmailId"]
    except ApiError as e:
        return (email, "failed", explain_create_error(e.status, e.text))

    try:
        shell = await client.create_connection(uid, profile["connection_name"], profile["connection_type"])
        cid = shell["ConnectionId"]
        payload = build_connection_payload(profile, expiry_time)
        await client.update_connection(uid, cid, payload)
    except ApiError as e:
        return (email, "failed", explain_create_error(e.status, e.text))

    warns = []
    try:
        await client.request("POST", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/oAuth",
                             json={"ResetOnNextLogin": False, "GeneratePassword": True, "SendPassword": True},
                             ok_statuses=list(range(200, 300)) + [409])
    except ApiError:
        warns.append("password email")

    if rec:
        try:
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Provisioning/", json={"EnableCallRecording": True})
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Provisioning/", json={"RecordAllCalls": True})
        except ApiError:
            warns.append("recording")

    if vm:
        try:
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}",
                                 json={"EnableDomainAdmin": False, "EnableVoicemail": True})
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Voicemail/",
                                 json={"Enabled": True, "Timeout": 30, "pin": 999, "EnableVoceimailNotify": True,
                                       "EnableWhatsAppNotify": False, "EnableTelegramNotify": False, "EnabledTranscribe": True})
        except ApiError:
            warns.append("voicemail")

    result = "warning" if warns else "success"
    detail = f"OK uid={uid} cid={cid}" + (f" (warns: {', '.join(warns)})" if warns else "")
    return (email, result, detail)

async def provision_flow(client, profile_type, single):
    title = ("CREATE PBX USER" if single else "BULK CREATE PBX USERS") if profile_type == "pbx" else \
            ("CREATE CONNEXCS USER" if single else "BULK CREATE CONNEXCS USERS")
    section(title)
    expiry_time = get_expiry_time()
    send_welcome = yes_no("Send welcome email? (y/n): ")
    rec = yes_no("Enable call recording? (y/n): ")
    vm = yes_no("Enable voicemail? (y/n): ")
    rows = collect_rows(profile_type, single)
    if not rows:
        print("No users provided.")
        return
    print("\nSummary")
    print("Profile:", profile_type.upper())
    print("Users:", len(rows))
    print("Expiry:", expiry_time.isoformat())
    print("Welcome email:", send_welcome)
    print("Recording:", rec)
    print("Voicemail:", vm)
    if not yes_no("\nCreate user(s)? (yes/no): "):
        return

    tasks = [provision_one(client, profile_type, row, expiry_time, send_welcome, rec, vm) for row in rows]
    results = []
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Provisioning", unit="user"):
        results.append(await coro)

    section("RESULTS")
    print_table(("Email", "Result", "Details"),
                [(e, (green if r == "success" else yellow if r == "warning" else red)(r.upper()), d) for e, r, d in results],
                (40, 10, 60))
    success = sum(1 for _, r, _ in results if r == "success")
    warnings = sum(1 for _, r, _ in results if r == "warning")
    failed = sum(1 for _, r, _ in results if r == "failed")
    print_summary(success, warnings, failed, len(results))
