import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from tqdm.asyncio import tqdm
from cli import section, ask, yes_no, print_summary, green, yellow, red
from config import EXPIRING_SOON_DAYS, RESEND_API_KEY, SENDER_EMAIL

import httpx

SEND_LOG_PATH = os.path.join(os.path.dirname(__file__), "send_log.json")
SEND_COOLDOWN = timedelta(hours=24)


def load_send_log():
    if not os.path.exists(SEND_LOG_PATH):
        return {}
    with open(SEND_LOG_PATH) as f:
        return json.load(f)


def save_send_log(log):
    with open(SEND_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def filter_recently_sent(selected, send_log):
    now = datetime.now(timezone.utc).isoformat()
    filtered = []
    skipped = []
    for entry in selected:
        email = entry[0]
        last = send_log.get(email)
        if last:
            last_dt = datetime.fromisoformat(last)
            if datetime.now(timezone.utc) - last_dt < SEND_COOLDOWN:
                skipped.append(email)
                continue
        filtered.append(entry)
    return filtered, skipped


def record_sent(send_log, email):
    send_log[email] = datetime.now(timezone.utc).isoformat()


def fmt_date(dt):
    return dt.strftime("%B %d, %Y")

def fmt_duration(dt):
    sec = int((dt - datetime.now(timezone.utc)).total_seconds())
    expired = sec < 0
    sec = abs(sec)
    d = sec // 86400
    h = (sec % 86400) // 3600
    if expired:
        return f"EXPIRED {d}d {h}h"
    if d > 0:
        return f"{d}d {h}h"
    return f"{h}h"


async def fetch_template(api_key, template_id):
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(
            f"https://api.resend.com/templates/{template_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if not r.is_success:
        raise Exception(f"Failed to fetch template: {r.status_code} {r.text[:200]}")
    data = r.json()
    html = data.get("html", "")
    subject = data.get("subject", "Reminder")
    return html, subject


def render_template(html, subject, first_name, due_date, days_left):
    html = html.replace("{{{first_name}}}", first_name)
    html = html.replace("{{{due_date}}}", due_date)
    html = html.replace("{{{days_left}}}", days_left)
    subject = subject.replace("{{{first_name}}}", first_name)
    subject = subject.replace("{{{due_date}}}", due_date)
    subject = subject.replace("{{{days_left}}}", days_left)
    return html, subject


async def audit_due(client):
    users = await client.get_users()
    cutoff = datetime.now(timezone.utc) + timedelta(days=EXPIRING_SOON_DAYS)
    rows = []

    async def process_user(u):
        email = u.get("UserEmail", "").lower()
        first_name = u.get("FirstName", email.split("@")[0])
        uid = u.get("UserEmailId")
        try:
            conns = await client.list_connections(uid)
        except Exception:
            return None

        async def resolve(c):
            name = c.get("Name", "")
            cid = c.get("ConnectionId")
            end = c.get("Expiry", {}).get("End") if isinstance(c.get("Expiry"), dict) else None
            if end:
                dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            else:
                try:
                    detail = await client.get_connection(uid, cid)
                    end = detail.get("Expiry", {}).get("End")
                    dt = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None
                except Exception:
                    dt = None
            if dt is None:
                return None
            dl = (dt - datetime.now(timezone.utc)).total_seconds() / 86400
            if dl >= 0 and dt > cutoff:
                return None
            return dt

        dates = [r for r in await asyncio.gather(*[resolve(c) for c in conns]) if r is not None]
        if not dates:
            return None
        earliest = min(dates)
        return (email, first_name, earliest)

    tasks = [process_user(u) for u in users]
    for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Auditing", unit="user"):
        result = await coro
        if result:
            rows.append(result)

    rows.sort(key=lambda x: x[2])
    return rows


async def send_email(api_key, sender, to_email, html, subject):
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": sender,
                "to": [to_email],
                "subject": subject,
                "html": html,
            },
        )
    if r.is_success:
        return (to_email, "sent")
    body = r.text[:500] if r.text else ""
    return (to_email, f"failed ({r.status_code}) {body}")


async def resolve_custom_email(client, email):
    email = email.lower()
    users = await client.get_users()
    for u in users:
        if u.get("UserEmail", "").lower() == email:
            first_name = u.get("FirstName", email.split("@")[0])
            uid = u.get("UserEmailId")
            try:
                conns = await client.list_connections(uid)
            except Exception:
                return (email, first_name, None)
            dates = []
            for c in conns:
                end = c.get("Expiry", {}).get("End") if isinstance(c.get("Expiry"), dict) else None
                if not end:
                    try:
                        detail = await client.get_connection(uid, c.get("ConnectionId"))
                        end = detail.get("Expiry", {}).get("End")
                    except Exception:
                        end = None
                if end:
                    try:
                        dates.append(datetime.fromisoformat(end.replace("Z", "+00:00")))
                    except Exception:
                        pass
            if dates:
                return (email, first_name, min(dates))
            return (email, first_name, None)
    return (email, email.split("@")[0], None)


async def notify_flow(client):
    section("SEND DUE DATE NOTIFICATIONS")

    api_key = RESEND_API_KEY or ask("Resend API key: ")
    sender = SENDER_EMAIL or ask("Sender email (verified in Resend): ")

    print("\nFetching email template...")
    try:
        template_html, template_subject = await fetch_template(api_key, "duedate")
    except Exception as e:
        print(red(f"Failed to load template: {e}"))
        return

    print("\nAuditing connections to find due users...")
    rows = await audit_due(client)

    if rows:
        print(f"\nDue users ({len(rows)}):")
        for i, (email, name, due) in enumerate(rows, 1):
            print(f"  {i:2d}. {email} ({name}) — {fmt_duration(due)}")
    else:
        print(yellow("\nNo due connections found in audit."))

    mode = ask("\nSend to (S)ingle, (B)ulk, (A)ll, or (C)ustom email: ", allow_empty=True).upper() or "A"

    selected = []
    if mode == "A":
        selected = rows
    elif mode == "C":
        custom = ask("Email address: ").lower()
        result = await resolve_custom_email(client, custom)
        selected = [result]
    elif mode == "S":
        nums = [int(x) for x in ask("Number from list: ").split(",") if x.strip().isdigit()]
        selected = [rows[i - 1] for i in nums if 1 <= i <= len(rows)]
    elif mode == "B":
        raw = ask("Numbers separated by commas (e.g. 1,3,5): ")
        nums = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        selected = [rows[i - 1] for i in nums if 1 <= i <= len(rows)]

    if not selected:
        print(red("No valid recipients selected."))
        return

    print(f"\nSelected {len(selected)} recipient(s):")
    for email, name, due in selected:
        due_str = fmt_duration(due) if due else "(no expiry found)"
        print(f"  {email} ({name}) — {due_str}")

    if not yes_no("\nSend? (yes/no): "):
        return

    send_log = load_send_log()
    filtered, skipped = filter_recently_sent(selected, send_log)
    if skipped:
        print(yellow(f"\nSkipped {len(skipped)} already sent within 24h: {', '.join(skipped)}"))
    if not filtered:
        print(red("All selected recipients were already sent to within 24h."))
        return
    selected = filtered

    tasks = []
    for email, first_name, due in selected:
        date_str = fmt_date(due) if due else "soon"
        left_str = fmt_duration(due) if due else "soon"
        html, subject = render_template(template_html, template_subject, first_name, date_str, left_str)
        tasks.append(send_email(api_key, sender, email, html, subject))

    results = []
    pbar = tqdm(total=len(tasks), desc="Sending", unit="email")
    for i in range(0, len(tasks), 2):
        batch = tasks[i:i+2]
        for coro in asyncio.as_completed(batch):
            results.append(await coro)
            pbar.update(1)
        if i + 2 < len(tasks):
            await asyncio.sleep(1)
    pbar.close()

    for email, status in results:
        if status == "sent":
            record_sent(send_log, email)
    save_send_log(send_log)

    section("RESULTS")
    sent = sum(1 for _, s in results if s == "sent")
    failed = sum(1 for _, s in results if s != "sent")
    print(f"Sent:   {green(str(sent))}")
    print(f"Failed: {red(str(failed))}")
    if failed:
        for email, status in results:
            if status != "sent":
                print(f"  {email}: {red(status)}")
    print_summary(sent, 0, failed, len(results))
