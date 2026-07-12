import asyncio
from datetime import datetime, timezone, timedelta
from tqdm.asyncio import tqdm
from cli import section, print_table, red, yellow, green
from config import EXPIRING_SOON_DAYS

def parse_date(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00"))

def days_left(expiry):
    return (expiry - datetime.now(timezone.utc)).total_seconds() / 86400

def fmt_expiry(expiry):
    now = datetime.now(timezone.utc)
    sec = int((expiry - now).total_seconds())
    expired = sec < 0
    sec = abs(sec)
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    if expired:
        return red(f"EXPIRED {d}d {h}h")
    if d > 0:
        return yellow(f"{d}d {h}h") if d <= EXPIRING_SOON_DAYS else green(f"{d}d {h}h")
    return yellow(f"{h}h {m}m")

def try_expiry(c):
    end = c.get("Expiry", {}).get("End") if isinstance(c.get("Expiry"), dict) else None
    return parse_date(end) if end else None

async def audit_flow(client, search=None):
    section("CONNECTION AUDIT")
    users = await client.get_users()

    if search:
        q = search.lower()
        users = [u for u in users if q in u.get("UserEmail", "").lower() or q in u.get("UserEmailId", "").lower()]
        if not users:
            print("No users match that search.")
            return

    total = len(users)
    cutoff = datetime.now(timezone.utc) + timedelta(days=EXPIRING_SOON_DAYS)

    no_expiry = []
    expired = []
    soon = []
    healthy_count = 0

    async def process_user(u):
        email = u.get("UserEmail", "")
        uid = u.get("UserEmailId")
        try:
            conns = await client.list_connections(uid)
        except Exception:
            return ("no_expiry", (email, "", "fetch failed"))

        if not conns:
            return ("no_expiry", (email, "", "no connections"))

        async def resolve(c):
            name = c.get("Name", "")
            cid = c.get("ConnectionId")
            dt = try_expiry(c)
            if dt is None:
                try:
                    detail = await client.get_connection(uid, cid)
                    end = detail.get("Expiry", {}).get("End")
                    dt = parse_date(end) if end else None
                except Exception:
                    dt = None
            if dt is None:
                return ("no_expiry", (email, name, cid))
            elif days_left(dt) < 0:
                return ("expired", (email, name, cid, dt))
            elif dt <= cutoff:
                return ("soon", (email, name, cid, dt))
            else:
                return ("healthy", None)

        return await asyncio.gather(*[resolve(c) for c in conns])

    tasks = [process_user(u) for u in users]
    for coro in tqdm(asyncio.as_completed(tasks), total=total, desc="Auditing", unit="user"):
        result = await coro
        if isinstance(result, tuple) and result[0] == "no_expiry":
            no_expiry.append(result[1])
            continue
        for tag, data in result:
            if tag == "no_expiry" and data:
                no_expiry.append(data)
            elif tag == "expired":
                expired.append(data)
            elif tag == "soon":
                soon.append(data)
            elif tag == "healthy":
                healthy_count += 1

    expired.sort(key=lambda x: x[3])
    soon.sort(key=lambda x: x[3])

    print(f"\n{'Total:':12s}{total}")
    print(f"{'Expired:':12s}{red(str(len(expired)))}")
    print(f"{'Expiring:':12s}{yellow(str(len(soon)))}")
    print(f"{'Healthy:':12s}{green(str(healthy_count))}")
    print(f"{'No expiry:':12s}{str(len(no_expiry))}")

    if no_expiry:
        section(f"MISSING EXPIRY ({len(no_expiry)})")
        print_table(("Email", "Connection", "Reason"), [(e, n, r) for e, n, r in no_expiry], (40, 20, 20))
    if expired:
        section(f"EXPIRED ({len(expired)})")
        print_table(("Email", "Connection", "Expired"), [(e, n, fmt_expiry(d)) for e, n, _, d in expired], (40, 20, 18))
    if soon:
        section(f"EXPIRING SOON ({len(soon)})")
        print_table(("Email", "Connection", "Remaining"), [(e, n, fmt_expiry(d)) for e, n, _, d in soon], (40, 20, 18))
    if not any([no_expiry, expired, soon]):
        print(green("\n All connections have valid expiry. No issues."))
