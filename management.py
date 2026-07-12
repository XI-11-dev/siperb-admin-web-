import asyncio
from datetime import datetime, timedelta, timezone
from tqdm.asyncio import tqdm
from cli import ask, yes_no, section, print_table, print_summary, green, yellow, red
from client import ApiError

async def parse_date(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

async def extend_one(client, email, days, users_cache):
    user = await client.find_user(email, users_cache)
    if not user:
        return (email, "failed", "user not found")
    uid = user["UserEmailId"]
    try:
        conns = await client.list_connections(uid)
    except ApiError as e:
        return (email, "failed", f"connections fetch failed ({e.status})")
    if not conns:
        return (email, "failed", "no connections")

    result = "success"
    details = []
    for c in conns:
        cid = c.get("ConnectionId")
        name = c.get("Name")
        try:
            detail = await client.get_connection(uid, cid)
            old = detail.get("Expiry", {}).get("End")
            if not old:
                details.append(f"{name}: no expiry")
                result = "warning"
                continue
            new = await parse_date(old) + timedelta(days=days)
            detail["Expiry"]["End"] = new.isoformat().replace("+00:00", "Z")
            await client.update_connection(uid, cid, detail)
            details.append(f"{name}: extended")
        except ApiError as e:
            details.append(f"{name}: failed ({e.status})")
            result = "warning"
    return (email, result, "; ".join(details))

async def extend_flow(client, single):
    section("EXTEND CONNECTION EXPIRY" if single else "BULK EXTEND CONNECTION EXPIRY")
    days = int(ask("Days to extend: "))
    if single:
        emails = [ask("Email: ").lower()]
    else:
        raw = ask("Emails separated by commas:\n")
        emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
    print("\nUsers:", len(emails))
    print("Days:", days)
    if not yes_no("Apply extension? (yes/no): "):
        return
    users_cache = await client.get_users()
    tasks = [extend_one(client, e, days, users_cache) for e in emails]
    results = []
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Extending", unit="user"):
        results.append(await coro)
    section("RESULTS")
    print_table(("Email", "Result", "Details"), [(e, (green if r == "success" else yellow if r == "warning" else red)(r.upper()), d) for e, r, d in results], (40, 10, 60))
    success = sum(1 for _, r, _ in results if r == "success")
    warnings = sum(1 for _, r, _ in results if r == "warning")
    failed = sum(1 for _, r, _ in results if r == "failed")
    print_summary(success, warnings, failed, len(results))

async def delete_one(client, email, users_cache):
    user = await client.find_user(email, users_cache)
    if not user:
        return (email, "failed", "user not found")
    uid = user["UserEmailId"]
    base = f"/Users/{client.owner_user_id}/DomainUsers/{uid}"
    warnings = []
    try:
        for c in await client.request("GET", f"{base}/Connections"):
            cid = c.get("ConnectionId") or c.get("Id")
            if cid:
                await client.request("DELETE", f"{base}/Connections/{cid}")
    except ApiError as e:
        warnings.append(f"conn cleanup ({e.status})")
    try:
        for d in await client.request("GET", f"{base}/Devices"):
            did = d.get("ProvisionToken") or d.get("DeviceId") or d.get("Id")
            if did:
                await client.request("DELETE", f"{base}/Devices/{did}")
    except ApiError as e:
        warnings.append(f"device cleanup ({e.status})")
    try:
        await client.request("DELETE", f"{base}/Voicemail/", ok_statuses=range(200, 500))
    except Exception:
        pass
    try:
        await client.request("DELETE", f"{base}?send_exit_email=no")
    except ApiError as e:
        return (email, "failed", f"delete failed ({e.status})")
    result = "warning" if warnings else "success"
    return (email, result, "; ".join(warnings) if warnings else "deleted")

async def delete_flow(client, single):
    section("DELETE USER" if single else "BULK DELETE USERS")
    if single:
        emails = [ask("Email to delete: ").lower()]
    else:
        raw = ask("Emails separated by commas:\n")
        emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
    print("Users to delete:", len(emails))
    if not yes_no("Delete user(s)? (yes/no): "):
        return
    users_cache = await client.get_users()
    tasks = [delete_one(client, e, users_cache) for e in emails]
    results = []
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Deleting", unit="user"):
        results.append(await coro)
    section("RESULTS")
    print_table(("Email", "Result", "Details"), [(e, (green if r == "success" else red)(r.upper()), d) for e, r, d in results], (40, 10, 60))
    success = sum(1 for _, r, _ in results if r == "success")
    warnings = sum(1 for _, r, _ in results if r == "warning")
    failed = sum(1 for _, r, _ in results if r == "failed")
    print_summary(success, warnings, failed, len(results))

async def set_caller_id_flow(client, single):
    section("CHANGE CALLER ID" if single else "BULK CHANGE CALLER ID")
    rows = []
    if single:
        email = ask("Email: ").lower()
        cid = ask("Caller ID: ")
        rows.append((email, cid))
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
            rows.append((parts[0].lower(), parts[1]))
    print("Users:", len(rows))
    if not yes_no("Update caller ID(s)? (yes/no): "):
        return

    users_cache = await client.get_users()
    results = []
    for email, new_cid in rows:
        user = await client.find_user(email, users_cache)
        if not user:
            results.append((email, "failed", "user not found"))
            continue
        uid = user["UserEmailId"]
        warns = []

        async def update_user():
            try:
                await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}",
                                     json={"Description": new_cid, "UserDescription": new_cid, "FirstName": new_cid})
            except ApiError as e:
                warns.append(f"user update ({e.status})")

        async def update_vcard():
            try:
                await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/vCard",
                                     json={"TitleDesc": new_cid})
            except ApiError as e:
                warns.append(f"vCard update ({e.status})")

        async def update_provisioning():
            try:
                await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Provisioning/",
                                     json={"profileName": new_cid})
            except ApiError as e:
                warns.append(f"provisioning update ({e.status})")

        await asyncio.gather(update_user(), update_vcard(), update_provisioning())

        try:
            conns = await client.list_connections(uid)
        except ApiError as e:
            results.append((email, "failed", f"list conns ({e.status})"))
            continue

        updated = False
        for c in conns:
            name = c.get("Name", "").lower()
            ctype = c.get("Type", "")
            if "connex" not in name or ctype != "OutboundTrunk":
                continue
            cid = c["ConnectionId"]
            try:
                detail = await client.get_connection(uid, cid)
                reg = detail.get("Registration", {})
                reg["username"] = new_cid
                reg["registrar_username"] = new_cid
                detail["Registration"] = reg
                await client.update_connection(uid, cid, detail)
                updated = True
            except ApiError as e:
                warns.append(f"conn update ({e.status})")

        if not conns:
            warns.append("no connections")
        elif not updated:
            warns.append("no ConnexCS connection found")

        result = "warning" if warns else "success"
        detail = "; ".join(warns) if warns else "caller ID updated"
        results.append((email, result, detail))

    section("RESULTS")
    print_table(("Email", "Result", "Details"),
                [(e, (green if r == "success" else yellow if r == "warning" else red)(r.upper()), d) for e, r, d in results],
                (40, 10, 60))
    success = sum(1 for _, r, _ in results if r == "success")
    warnings = sum(1 for _, r, _ in results if r == "warning")
    failed = sum(1 for _, r, _ in results if r == "failed")
    print_summary(success, warnings, failed, len(results))


REFRESH_COPY_KEYS = [
    "Name", "Type", "DialPattern", "Weight", "Disabled", "AllowSrc",
    "CustomHeaders", "Expiry", "Registration", "SbcServer", "Transcoding", "TimeOfDayRouting"
]

async def refresh_connection(client, email, users_cache=None):
    user = await client.find_user(email, users_cache if users_cache else await client.get_users())
    if not user:
        return (email, "failed", "user not found")

    uid = user["UserEmailId"]
    try:
        conns = await client.list_connections(uid)
    except ApiError as e:
        return (email, "failed", f"connections fetch failed ({e.status})")

    if not conns:
        return (email, "failed", "no connections to refresh")

    results = []
    for c in conns:
        cid = c.get("ConnectionId")
        name = c.get("Name", "")
        conn_type = c.get("Type", "")

        try:
            detail = await client.get_connection(uid, cid)
        except ApiError as e:
            results.append(f"{name}: get detail failed ({e.status})")
            continue

        payload = {}
        for key in REFRESH_COPY_KEYS:
            if key in detail:
                payload[key] = detail[key]

        try:
            await client.delete_connection(uid, cid)
        except ApiError as e:
            results.append(f"{name}: delete failed ({e.status})")
            continue

        await asyncio.sleep(0.5)

        try:
            shell = await client.create_connection(uid, name, conn_type)
            new_cid = shell["ConnectionId"]
            await client.update_connection(uid, new_cid, payload)
            results.append(f"{name}: refreshed ({cid} -> {new_cid})")
        except ApiError as e:
            results.append(f"{name}: recreate failed ({e.status})")

    result = "success" if all("failed" not in r for r in results) else "warning"
    return (email, result, "; ".join(results))

async def refresh_flow(client, single):
    section("REFRESH CONNECTION" if single else "BULK REFRESH CONNECTIONS")
    if single:
        emails = [ask("Email: ").lower()]
    else:
        raw = ask("Emails separated by commas:\n")
        emails = [e.strip().lower() for e in raw.split(",") if e.strip()]

    print("Users to refresh:", len(emails))
    if not yes_no("Refresh connection(s)? This will delete and recreate them. (yes/no): "):
        return

    users_cache = await client.get_users()
    tasks = [refresh_connection(client, e, users_cache) for e in emails]
    results = []
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Refreshing", unit="user"):
        results.append(await coro)

    section("RESULTS")
    print_table(
        ("Email", "Result", "Details"),
        [(e, (green if r == "success" else yellow if r == "warning" else red)(r.upper()), d) for e, r, d in results],
        (40, 10, 70)
    )
    success = sum(1 for _, r, _ in results if r == "success")
    warnings = sum(1 for _, r, _ in results if r == "warning")
    failed = sum(1 for _, r, _ in results if r == "failed")
    print_summary(success, warnings, failed, len(results))
