import asyncio
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone, timedelta

import streamlit as st

from client import SiperbClient, ApiError
from config import EXPIRING_SOON_DAYS, RESEND_API_KEY, SENDER_EMAIL
from connexcs_did import ConnexCSClient

st.set_page_config(page_title="Eleven Solutions LLC", page_icon="📞", layout="wide")

# ── Theme override ────────────────────────────────────────────
st.markdown("""
<style>
#root > div:first-child > div:first-child { background: #000 !important; }
.stApp { background: #000 !important; }
.stSidebar, .css-1d391kg, .css-1lcbmhc, section[data-testid="stSidebar"] { background: #111 !important; }
h1, h2, h3, h4 { color: #FF6B00 !important; }
.stMarkdown, .stText, p, li, label { color: #fff !important; }
.stButton > button { background: #FF6B00 !important; color: #000 !important; border: none !important; font-weight: bold !important; }
.stButton > button:hover { background: #FF8C33 !important; color: #000 !important; }
.stButton > button:active { background: #E65C00 !important; }
.stTextInput > div > div { background: #222 !important; border: 1px solid #FF6B00 !important; color: #fff !important; }
.stTextInput input { color: #fff !important; }
.stTextArea > div > div { background: #222 !important; border: 1px solid #FF6B00 !important; color: #fff !important; }
.stTextArea textarea { color: #fff !important; }
.stSelectbox > div > div { background: #222 !important; border: 1px solid #FF6B00 !important; color: #fff !important; }
.stMultiSelect > div > div { background: #222 !important; border: 1px solid #FF6B00 !important; color: #fff !important; }
.stNumberInput > div > div { background: #222 !important; border: 1px solid #FF6B00 !important; color: #fff !important; }
.stNumberInput input { color: #fff !important; }
.stCheckbox label { color: #fff !important; }
.stRadio > div { color: #fff !important; }
.stRadio label { color: #fff !important; }
.css-1m6wrjf, .css-16txtl3, .stMetric { background: #111 !important; border: 1px solid #333 !important; border-radius: 8px !important; padding: 12px !important; }
.stMetric label { color: #FF6B00 !important; }
.stMetric .css-1xarl3l, .stMetric .css-qrbaxs { color: #fff !important; }
.stSuccess { background: #0f3 !important; color: #000 !important; }
.stError { background: #f33 !important; color: #fff !important; }
.stWarning { background: #FF6B00 !important; color: #000 !important; }
.stInfo { background: #222 !important; border: 1px solid #FF6B00 !important; color: #fff !important; }
.stDataFrame { background: #111 !important; color: #fff !important; }
.stDataFrame table { color: #fff !important; }
div[data-testid="stExpander"] { background: #111 !important; border: 1px solid #333 !important; }
.stCode { background: #222 !important; color: #FF6B00 !important; }
.stSpinner > div { border-top-color: #FF6B00 !important; }
hr { border-color: #333 !important; }
</style>
""", unsafe_allow_html=True)

# ── Persistent event loop ────────────────────────────────────
if "_loop" not in st.session_state:
    st.session_state._loop = asyncio.new_event_loop()
loop = st.session_state._loop
asyncio.set_event_loop(loop)

def run(coro):
    return loop.run_until_complete(coro)

# ── Client init ──────────────────────────────────────────────
def get_client():
    if "_client" not in st.session_state:
        c = SiperbClient()
        run(c.login())
        st.session_state._client = c
    return st.session_state._client

# ── Send log (in-memory for cloud) ───────────────────────────
def get_send_log():
    if "_send_log" not in st.session_state:
        st.session_state._send_log = {}
    return st.session_state._send_log

def record_sent(email):
    log = get_send_log()
    log[email] = datetime.now(timezone.utc).isoformat()
    st.session_state._send_log = log

def filter_recently_sent(emails):
    now = datetime.now(timezone.utc)
    filtered = []
    skipped = []
    log = get_send_log()
    for email in emails:
        last = log.get(email)
        if last:
            last_dt = datetime.fromisoformat(last)
            if now - last_dt < timedelta(hours=24):
                skipped.append(email)
                continue
        filtered.append(email)
    return filtered, skipped

# ── Capture helper for CLI functions ─────────────────────────
def run_captured(fn, label="Working..."):
    buf = io.StringIO()
    with st.spinner(label), redirect_stdout(buf), redirect_stderr(buf):
        try:
            result = fn()
            out = buf.getvalue()
            if out.strip():
                st.text(out)
            return result
        except Exception as e:
            out = buf.getvalue()
            if out.strip():
                st.text(out)
            st.error(str(e))
            return None

# ── Web-native Siperb helpers ────────────────────────────────
async def find_user(client, email):
    users = await client.get_users()
    for u in users:
        if u.get("UserEmail", "").lower() == email.lower():
            return u
    return None

async def list_connections(client, uid):
    return await client.request("GET", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Connections")

async def extend_one(client, email, days):
    user = await find_user(client, email)
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
            dt = datetime.fromisoformat(old.replace("Z", "+00:00"))
            dt += timedelta(days=days)
            detail["Expiry"]["End"] = dt.isoformat().replace("+00:00", "Z")
            await client.update_connection(uid, cid, detail)
            details.append(f"{name}: extended {days}d")
        except ApiError as e:
            details.append(f"{name}: failed ({e.status})")
            result = "warning"
    return (email, result, "; ".join(details))

async def delete_one(client, email):
    user = await find_user(client, email)
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

async def provision_user(client, email, profile_type, caller_id=None, rec=False, vm=False):
    from profiles import build_connexcs_profile, build_pbx_profile, build_connection_payload
    from config import TRIAL_HOURS, TRIAL_MINUTES
    user = await find_user(client, email)
    if user:
        return (email, "failed", "already exists")
    if profile_type == "pbx":
        profile = build_pbx_profile("", "", caller_id or "")
    else:
        profile = build_connexcs_profile(caller_id or email.split("@")[0])
    expiry = datetime.now(timezone.utc) + timedelta(hours=TRIAL_HOURS, minutes=TRIAL_MINUTES)
    try:
        data = await client.request("POST", f"/Users/{client.owner_user_id}/DomainUsers",
                                    json={"Email": email, "UserType": "Internal",
                                          "Description": profile["description"],
                                          "JoinMailingList": True, "SendWelcomeEmail": False})
        uid = data["UserEmailId"]
    except ApiError as e:
        return (email, "failed", "create failed")
    try:
        shell = await client.create_connection(uid, profile["connection_name"], profile["connection_type"])
        cid = shell["ConnectionId"]
        payload = build_connection_payload(profile, expiry)
        await client.update_connection(uid, cid, payload)
    except ApiError as e:
        return (email, "failed", "connection failed")
    warns = []
    try:
        await client.request("POST", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/oAuth",
                             json={"ResetOnNextLogin": False, "GeneratePassword": True, "SendPassword": True},
                             ok_statuses=list(range(200, 300)) + [409])
    except ApiError:
        warns.append("password email")
    if rec:
        try:
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Provisioning/",
                                 json={"EnableCallRecording": True})
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Provisioning/",
                                 json={"RecordAllCalls": True})
        except ApiError:
            warns.append("recording")
    if vm:
        try:
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}",
                                 json={"EnableDomainAdmin": False, "EnableVoicemail": True})
            await client.request("PUT", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Voicemail/",
                                 json={"Enabled": True, "Timeout": 30, "pin": 999,
                                       "EnableVoceimailNotify": True, "EnableWhatsAppNotify": False,
                                       "EnableTelegramNotify": False, "EnabledTranscribe": True})
        except ApiError:
            warns.append("voicemail")
    result = "warning" if warns else "success"
    detail = f"OK uid={uid} cid={cid}" + (f" (warns: {', '.join(warns)})" if warns else "")
    return (email, result, detail)

async def refresh_one(client, email):
    user = await find_user(client, email)
    if not user:
        return (email, "failed", "user not found")
    uid = user["UserEmailId"]
    try:
        conns = await client.list_connections(uid)
    except ApiError as e:
        return (email, "failed", f"connections fetch failed ({e.status})")
    if not conns:
        return (email, "failed", "no connections to refresh")
    REFRESH_COPY_KEYS = [
        "Name", "Type", "DialPattern", "Weight", "Disabled", "AllowSrc",
        "CustomHeaders", "Expiry", "Registration", "SbcServer", "Transcoding", "TimeOfDayRouting"
    ]
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

async def change_caller_id_one(client, email, new_cid):
    user = await find_user(client, email)
    if not user:
        return (email, "failed", "user not found")
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
        return (email, "failed", f"list conns ({e.status})")
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
    return (email, result, detail)

# ── Notification helpers (web-native) ─────────────────────────
from notify import fetch_template, render_template, send_email, fmt_date, fmt_duration, audit_due, resolve_custom_email

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("<h1 style='color:#FF6B00; margin-bottom:0;'>Eleven</h1>"
                "<h2 style='color:#fff; margin-top:0;'>Solutions LLC</h2>", unsafe_allow_html=True)
    st.markdown("---")
    pages = [
        "Dashboard", "Create User", "Extend Expiry", "Delete User",
        "Refresh Connection", "Change Caller ID", "Audit",
        "Send Notifications", "ConnexCS DID",
    ]
    page = st.radio("", pages, key="nav", label_visibility="collapsed")
    st.markdown("---")
    st.caption("v6.4 Web")

# ══════════════════════════════════════════════════════════════
if page == "Dashboard":
    st.title("Dashboard")
    try:
        client = get_client()
        billing, owner = run(asyncio.gather(
            client.request("GET", f"/Users/{client.owner_user_id}/Billing"),
            client.get_owner_profile(),
        ))
        subs = billing.get("Subscriptions", [])
        total = subs[0].get("Quantity", "?") if subs else "?"
        dus = owner.get("DomainUsers", [])
        occ = len(dus) if isinstance(dus, list) else 0
        col1, col2, col3 = st.columns(3)
        col1.metric("Seats Used", occ)
        avail = int(total) - occ if isinstance(total, int) else "?"
        col2.metric("Total Seats", total)
        col3.metric("Available", avail)
    except Exception as e:
        st.error(f"Connection failed: {e}")

# ══════════════════════════════════════════════════════════════
elif page == "Audit":
    st.title("Connection Audit")
    if st.button("Run Audit", type="primary"):
        client = get_client()
        users = run(client.get_users())
        total = len(users)
        cutoff = datetime.now(timezone.utc) + timedelta(days=EXPIRING_SOON_DAYS)
        from audit import parse_date, days_left
        no_expiry = []
        expired = []
        soon = []
        healthy_count = 0
        pbar = st.progress(0, text="Auditing...")
        status_text = st.empty()
        for idx, u in enumerate(users):
            email = u.get("UserEmail", "")
            uid = u.get("UserEmailId")
            status_text.text(f"Processing {email}...")
            try:
                conns = run(client.list_connections(uid))
            except Exception:
                no_expiry.append((email, "", "fetch failed"))
                pbar.progress((idx + 1) / total)
                continue
            if not conns:
                no_expiry.append((email, "", "no connections"))
                pbar.progress((idx + 1) / total)
                continue
            for c in conns:
                name = c.get("Name", "")
                cid = c.get("ConnectionId")
                end = c.get("Expiry", {}).get("End") if isinstance(c.get("Expiry"), dict) else None
                if end:
                    dt = parse_date(end)
                else:
                    try:
                        detail = run(client.get_connection(uid, cid))
                        end = detail.get("Expiry", {}).get("End")
                        dt = parse_date(end) if end else None
                    except Exception:
                        dt = None
                if dt is None:
                    no_expiry.append((email, name, cid))
                elif days_left(dt) < 0:
                    expired.append((email, name, cid, dt))
                elif dt <= cutoff:
                    soon.append((email, name, cid, dt))
                else:
                    healthy_count += 1
            pbar.progress((idx + 1) / total)
        status_text.empty()
        pbar.empty()
        expired.sort(key=lambda x: x[3])
        soon.sort(key=lambda x: x[3])
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total", total)
        col2.metric("Expired", len(expired))
        col3.metric("Expiring Soon", len(soon))
        col4.metric("Healthy", healthy_count)
        if no_expiry:
            with st.expander(f"Missing Expiry ({len(no_expiry)})", expanded=True):
                for e, n, r in no_expiry:
                    st.warning(f"**{e}** — {n} ({r})")
        if expired:
            with st.expander(f"Expired ({len(expired)})", expanded=True):
                st.dataframe(
                    [{"Email": e, "Connection": n, "Expired": f"{(datetime.now(timezone.utc)-d).days}d ago"} for e, n, _, d in expired],
                    use_container_width=True, hide_index=True
                )
        if soon:
            with st.expander(f"Expiring Soon ({len(soon)})", expanded=True):
                rows = []
                for e, n, _, d in soon:
                    dl = (d - datetime.now(timezone.utc)).total_seconds() / 86400
                    rows.append({"Email": e, "Connection": n, "Days Left": f"{dl:.1f}d"})
                st.dataframe(rows, use_container_width=True, hide_index=True)
        if not any([no_expiry, expired, soon]):
            st.success("All connections have valid expiry. No issues.")

# ══════════════════════════════════════════════════════════════
elif page == "Create User":
    st.title("Create User")
    typ = st.selectbox("Profile Type", ["pbx", "connexcs"])
    mode = st.radio("Mode", ["Single", "Bulk"], horizontal=True)
    emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com")
    if mode == "Single":
        caller_id = st.text_input("Caller ID (optional)")
    else:
        bulk_caller_ids = st.text_area("Caller IDs (one per line, same order as emails, optional)")
    rec = typ == "pbx" and st.checkbox("Enable call recording")
    vm = typ == "pbx" and st.checkbox("Enable voicemail")
    if st.button("Create", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        else:
            client = get_client()
            lines = [l.strip() for l in emails_raw.strip().splitlines() if l.strip()]
            cid_lines = []
            if mode == "Single":
                cid_lines = [caller_id] if caller_id else [None]
            else:
                if bulk_caller_ids.strip():
                    cid_lines = [l.strip() for l in bulk_caller_ids.strip().splitlines() if l.strip()]
                while len(cid_lines) < len(lines):
                    cid_lines.append(None)
            results = []
            with st.spinner("Creating..."):
                for i, email in enumerate(lines):
                    result = run(provision_user(client, email, typ, cid_lines[i] if i < len(cid_lines) and cid_lines[i] else None, rec, vm))
                    results.append(result)
            st.markdown("---")
            st.subheader("Results")
            success = sum(1 for _, r, _ in results if r == "success")
            warns = sum(1 for _, r, _ in results if r == "warning")
            failed = sum(1 for _, r, _ in results if r == "failed")
            col1, col2, col3 = st.columns(3)
            col1.metric("Successful", success)
            col2.metric("Warnings", warns)
            col3.metric("Failed", failed)
            for email, status, detail in results:
                if status == "success":
                    st.success(f"{email}: {detail}")
                elif status == "warning":
                    st.warning(f"{email}: {detail}")
                else:
                    st.error(f"{email}: {detail}")

# ══════════════════════════════════════════════════════════════
elif page == "Extend Expiry":
    st.title("Extend Connection Expiry")
    days = st.number_input("Days to extend", min_value=1, value=30)
    emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com")
    confirm = st.checkbox("I confirm I want to extend the above users")
    if st.button("Extend", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        elif not confirm:
            st.warning("Please confirm the action.")
        else:
            client = get_client()
            lines = [l.strip() for l in emails_raw.strip().splitlines() if l.strip()]
            results = []
            with st.spinner("Extending..."):
                for email in lines:
                    results.append(run(extend_one(client, email, days)))
            st.markdown("---")
            st.subheader("Results")
            success = sum(1 for _, r, _ in results if r == "success")
            warns = sum(1 for _, r, _ in results if r == "warning")
            failed = sum(1 for _, r, _ in results if r == "failed")
            col1, col2, col3 = st.columns(3)
            col1.metric("Successful", success)
            col2.metric("Warnings", warns)
            col3.metric("Failed", failed)
            for email, status, detail in results:
                if status == "success":
                    st.success(f"{email}: {detail}")
                elif status == "warning":
                    st.warning(f"{email}: {detail}")
                else:
                    st.error(f"{email}: {detail}")

# ══════════════════════════════════════════════════════════════
elif page == "Delete User":
    st.title("Delete User")
    emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com")
    confirm = st.checkbox("I confirm I want to delete the above users permanently")
    if st.button("Delete", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        elif not confirm:
            st.warning("Please confirm the action.")
        else:
            client = get_client()
            lines = [l.strip() for l in emails_raw.strip().splitlines() if l.strip()]
            results = []
            with st.spinner("Deleting..."):
                for email in lines:
                    results.append(run(delete_one(client, email)))
            st.markdown("---")
            st.subheader("Results")
            success = sum(1 for _, r, _ in results if r == "success")
            warns = sum(1 for _, r, _ in results if r == "warning")
            failed = sum(1 for _, r, _ in results if r == "failed")
            col1, col2, col3 = st.columns(3)
            col1.metric("Deleted", success)
            col2.metric("Warnings", warns)
            col3.metric("Failed", failed)
            for email, status, detail in results:
                if status == "success":
                    st.success(f"{email}: {detail}")
                elif status == "warning":
                    st.warning(f"{email}: {detail}")
                else:
                    st.error(f"{email}: {detail}")

# ══════════════════════════════════════════════════════════════
elif page == "Refresh Connection":
    st.title("Refresh Connection")
    st.warning("This will delete and recreate the connection. The user may experience a brief interruption.")
    emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com")
    confirm = st.checkbox("I confirm I want to refresh connections for the above users")
    if st.button("Refresh", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        elif not confirm:
            st.warning("Please confirm the action.")
        else:
            client = get_client()
            lines = [l.strip() for l in emails_raw.strip().splitlines() if l.strip()]
            results = []
            with st.spinner("Refreshing..."):
                for email in lines:
                    results.append(run(refresh_one(client, email)))
            st.markdown("---")
            st.subheader("Results")
            success = sum(1 for _, r, _ in results if r == "success")
            warns = sum(1 for _, r, _ in results if r == "warning")
            failed = sum(1 for _, r, _ in results if r == "failed")
            col1, col2, col3 = st.columns(3)
            col1.metric("Successful", success)
            col2.metric("Warnings", warns)
            col3.metric("Failed", failed)
            for email, status, detail in results:
                if status == "success":
                    st.success(f"{email}: {detail}")
                elif status == "warning":
                    st.warning(f"{email}: {detail}")
                else:
                    st.error(f"{email}: {detail}")

# ══════════════════════════════════════════════════════════════
elif page == "Change Caller ID":
    st.title("Change Caller ID")
    mode = st.radio("Mode", ["Single", "Bulk"], horizontal=True)
    if mode == "Single":
        email = st.text_input("Email")
        caller_id = st.text_input("New Caller ID")
        if st.button("Update", type="primary"):
            if not email or not caller_id:
                st.warning("Enter email and caller ID.")
            else:
                client = get_client()
                with st.spinner("Updating..."):
                    result = run(change_caller_id_one(client, email, caller_id))
                    if result[1] == "success":
                        st.success(f"{result[0]}: {result[2]}")
                    elif result[1] == "warning":
                        st.warning(f"{result[0]}: {result[2]}")
                    else:
                        st.error(f"{result[0]}: {result[2]}")
    else:
        rows_raw = st.text_area("Rows: email, callerid (one per line)", placeholder="user@example.com,5551234")
        if st.button("Update All", type="primary"):
            if not rows_raw.strip():
                st.warning("Enter at least one row.")
            else:
                client = get_client()
                rows = []
                for line in rows_raw.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2:
                        rows.append((parts[0], parts[1]))
                    else:
                        st.warning(f"Skipped invalid: {line}")
                if rows:
                    st.markdown(f"Updating {len(rows)} user(s)...")
                    with st.spinner("Updating..."):
                        results = [run(change_caller_id_one(client, e, c)) for e, c in rows]
                    st.markdown("---")
                    st.subheader("Results")
                    for email, status, detail in results:
                        if status == "success":
                            st.success(f"{email}: {detail}")
                        elif status == "warning":
                            st.warning(f"{email}: {detail}")
                        else:
                            st.error(f"{email}: {detail}")

# ══════════════════════════════════════════════════════════════
elif page == "Send Notifications":
    st.title("Send Due Date Notifications")
    api_key = st.text_input("Resend API key", value=RESEND_API_KEY or "", type="password")
    sender = st.text_input("Sender email", value=SENDER_EMAIL or "billing@elev1solutions.com")
    tab1, tab2 = st.tabs(["Audit & Send", "Send to Specific Email"])
    with tab1:
        st.info("Audit all users and send due-date reminders to selected recipients.")
        if st.button("1. Fetch Due Users", type="primary"):
            if not api_key:
                st.warning("Enter API key first.")
            else:
                st.session_state._due_rows = None
                st.session_state._template = None
                client = get_client()
                with st.spinner("Fetching email template..."):
                    try:
                        html, subject = run(fetch_template(api_key, "duedate"))
                        st.session_state._template = (html, subject)
                        st.success("Template loaded.")
                    except Exception as ex:
                        st.error(f"Template failed: {ex}")
                        html, subject = None, None
                with st.spinner("Auditing connections..."):
                    try:
                        rows = run(audit_due(client))
                        st.session_state._due_rows = rows
                    except Exception as ex:
                        st.error(f"Audit failed: {ex}")
                        rows = []
                if rows:
                    st.success(f"Found {len(rows)} due user(s).")
                    st.dataframe(
                        [{"#": i+1, "Email": r[0], "Name": r[1], "Due": fmt_duration(r[2]), "Date": fmt_date(r[2])} for i, r in enumerate(rows)],
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.info("No due users found.")
        if st.session_state.get("_due_rows"):
            rows = st.session_state._due_rows
            template = st.session_state.get("_template")
            all_emails = [r[0] for r in rows]
            mode = st.radio("Send mode", ["All", "Select from list", "Custom email"], horizontal=True)
            selected = []
            if mode == "All":
                selected = rows
            elif mode == "Select from list":
                labels = [f"{i+1}. {r[0]} ({r[1]}) — {fmt_duration(r[2])}" for i, r in enumerate(rows)]
                picks = st.multiselect("Select recipients", labels)
                indices = [labels.index(p) for p in picks]
                selected = [rows[i] for i in indices]
            elif mode == "Custom email":
                custom = st.text_input("Email address")
                if custom:
                    client = get_client()
                    with st.spinner("Resolving..."):
                        result = run(resolve_custom_email(client, custom))
                        selected = [result] if result[2] else []
                        if not selected:
                            st.warning("No expiry found for that email.")
            if selected and template:
                st.markdown(f"**{len(selected)} recipient(s) selected**")
                for r in selected:
                    st.text(f"{r[0]} ({r[1]}) — {fmt_duration(r[2])}")
                if st.button("2. Send Reminders", type="primary"):
                    html, subject = template
                    filtered_emails = [r[0] for r in selected]
                    filtered, skipped = filter_recently_sent(filtered_emails)
                    if skipped:
                        st.warning(f"Skipped {len(skipped)} already sent within 24h: {', '.join(skipped)}")
                    if not filtered:
                        st.error("All selected recipients were already sent to within 24h.")
                    else:
                        with st.spinner("Sending..."):
                            results = []
                            for i in range(0, len(filtered), 2):
                                batch = filtered[i:i+2]
                                for email in batch:
                                    match = [r for r in selected if r[0] == email][0]
                                    _, first_name, due = match
                                    date_str = fmt_date(due) if due else "soon"
                                    left_str = fmt_duration(due) if due else "soon"
                                    h, s = render_template(html, subject, first_name, date_str, left_str)
                                    result = run(send_email(api_key, sender, email, h, s))
                                    results.append(result)
                                    if "sent" in result[1]:
                                        record_sent(email)
                                if i + 2 < len(filtered):
                                    import time
                                    time.sleep(1)
                        sent = sum(1 for _, s in results if s == "sent")
                        failed = sum(1 for _, s in results if s != "sent")
                        st.markdown("---")
                        st.subheader("Results")
                        col1, col2 = st.columns(2)
                        col1.metric("Sent", sent)
                        col2.metric("Failed", failed)
                        for email, status in results:
                            if status == "sent":
                                st.success(f"{email}: {status}")
                            else:
                                st.error(f"{email}: {status}")
    with tab2:
        emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com", key="notify_custom")
        if st.button("Send to Listed Emails", type="primary"):
            if not emails_raw.strip():
                st.warning("Enter at least one email.")
            elif not api_key:
                st.warning("Enter your Resend API key.")
            else:
                client = get_client()
                with st.spinner("Sending..."):
                    lines = [l.strip() for l in emails_raw.strip().splitlines() if l.strip()]
                    st.session_state._template = None
                    try:
                        html, subject = run(fetch_template(api_key, "duedate"))
                    except Exception as ex:
                        st.error(f"Template: {ex}")
                        html, subject = None, None
                    if html:
                        results = []
                        for email in lines:
                            user = run(find_user(client, email))
                            if not user:
                                results.append((email, "user not found"))
                                continue
                            first_name = user.get("FirstName", email.split("@")[0])
                            uid = user["UserEmailId"]
                            conns = run(list_connections(client, uid))
                            dates = []
                            for c in conns:
                                end = c.get("Expiry", {}).get("End") if isinstance(c.get("Expiry"), dict) else None
                                if not end:
                                    try:
                                        detail = run(client.get_connection(uid, c.get("ConnectionId")))
                                        end = detail.get("Expiry", {}).get("End")
                                    except Exception:
                                        end = None
                                if end:
                                    try:
                                        dates.append(datetime.fromisoformat(end.replace("Z", "+00:00")))
                                    except Exception:
                                        pass
                            if not dates:
                                results.append((email, "no expiry found"))
                                continue
                            due = min(dates)
                            h, s = render_template(html, subject, first_name, fmt_date(due), fmt_duration(due))
                            res = run(send_email(api_key, sender, email, h, s))
                            results.append(res)
                            if "sent" in res[1]:
                                record_sent(email)
                        for email, status in results:
                            if "sent" in status:
                                st.success(f"{email}: {status}")
                            else:
                                st.error(f"{email}: {status}")

# ══════════════════════════════════════════════════════════════
elif page == "ConnexCS DID":
    st.title("ConnexCS DID Manager")
    did_action = st.radio("Action", ["Inventory", "Assign", "Unassign", "Transcript"], horizontal=True)
    did_client = ConnexCSClient()
    if did_action == "Inventory":
        if st.button("Refresh Inventory"):
            with st.spinner("Fetching DIDs..."):
                try:
                    all_dids = did_client.fetch_all_dids()
                    unassigned = [d for d in all_dids if not d.get("customer_id")]
                    assigned = [d for d in all_dids if d.get("customer_id")]
                    cols = st.columns(3)
                    cols[0].metric("Total DIDs", len(all_dids))
                    cols[1].metric("Unassigned (Inventory)", len(unassigned))
                    cols[2].metric("Assigned", len(assigned))
                    if unassigned:
                        st.subheader(f"Unassigned DIDs ({len(unassigned)})")
                        rows = []
                        for d in unassigned:
                            tags = ", ".join(d["tags"]) if isinstance(d.get("tags"), list) else str(d.get("tags") or "")
                            rows.append({"DID": d["did"], "Tags": tags, "ID": d["id"]})
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    if assigned:
                        with st.expander(f"Assigned DIDs ({len(assigned)})"):
                            rows = []
                            for d in assigned:
                                tags = ", ".join(d["tags"]) if isinstance(d.get("tags"), list) else str(d.get("tags") or "")
                                rows.append({"DID": d["did"], "Customer": d.get("customer_id", ""), "Tags": tags})
                            st.dataframe(rows, use_container_width=True, hide_index=True)
                    if not unassigned:
                        st.info("No unassigned DIDs in inventory.")
                except Exception as ex:
                    st.error(f"Error: {ex}")
    elif did_action == "Assign":
        st.subheader("Assign DIDs to Customer")
        with st.spinner("Loading DIDs and customers..."):
            try:
                unassigned = did_client.fetch_all_unassigned()
                if not unassigned:
                    st.warning("No unassigned DIDs available.")
                else:
                    did_options = {f"{d['did']}  [{', '.join(d['tags']) if isinstance(d.get('tags'), list) else d.get('tags','')}]": d for d in unassigned}
                    selected_labels = st.multiselect("Select DIDs to assign", list(did_options.keys()))
                    customers = did_client.get_customers()
                    if customers:
                        cust_options = {f"{c.get('name') or c.get('company_name') or c.get('email','(no name)')} (id={c['id']})": c for c in customers}
                        cust_label = st.selectbox("Select Customer", list(cust_options.keys()))
                        customer = cust_options[cust_label]
                        ips = did_client.get_customer_ips(customer["id"])
                        hosts = sorted(set(
                            (r.get("fqdn") or r.get("ip") or "").strip()
                            for r in ips if r.get("fqdn") or r.get("ip")
                        ))
                        host_options = hosts + ["Enter custom IP/host"]
                        dest_label = st.selectbox("Select Destination", host_options)
                        if dest_label == "Enter custom IP/host":
                            dest_host = st.text_input("Enter IP/host")
                        else:
                            dest_host = dest_label
                        tags_input = st.text_input("Tags (comma-separated, optional)")
                        if selected_labels and dest_host and st.button("Assign DIDs"):
                            new_tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []
                            with st.spinner(f"Assigning {len(selected_labels)} DID(s)..."):
                                for label in selected_labels:
                                    d = did_options[label]
                                    try:
                                        full = did_client.get_did(d["id"])
                                        full["customer_id"] = customer["id"]
                                        full["destination"] = f"{d['did']}@{dest_host}"
                                        full["destination_type"] = "uri"
                                        if new_tags:
                                            existing = full.get("tags") or []
                                            merged = existing[:]
                                            for t in new_tags:
                                                if t not in merged:
                                                    merged.append(t)
                                            full["tags"] = merged
                                        did_client.update_did(d["id"], full)
                                        st.success(f"{d['did']} -> {full['destination']}")
                                    except Exception as ex:
                                        st.error(f"{d['did']}: {ex}")
                    else:
                        st.error("No active customers found.")
            except Exception as ex:
                st.error(f"Error: {ex}")
    elif did_action == "Unassign":
        st.subheader("Unassign DIDs")
        raw = st.text_input("Enter DIDs to unassign (comma-separated)")
        if raw and st.button("Unassign"):
            search = [n.strip() for n in raw.split(",") if n.strip()]
            with st.spinner(f"Searching for {len(search)} number(s)..."):
                all_dids = did_client.fetch_all_dids()
                found = [d for d in all_dids if d["did"] in search]
                not_found = [n for n in search if n not in [d["did"] for d in found]]
                if not_found:
                    st.warning(f"Not found: {', '.join(not_found)}")
                to_unassign = []
                for d in found:
                    if d.get("customer_id"):
                        to_unassign.append(d)
                    else:
                        st.info(f"{d['did']} -> already unassigned, skipping")
                if not to_unassign:
                    st.info("Nothing to unassign.")
                else:
                    for d in to_unassign:
                        try:
                            full = did_client.get_did(d["id"])
                            full["customer_id"] = None
                            did_client.update_did(d["id"], full)
                            st.success(f"{d['did']} -> returned to inventory")
                        except Exception as ex:
                            st.error(f"{d['did']}: {ex}")
    elif did_action == "Transcript":
        st.subheader("Pull Call Transcript")
        callid = st.text_input("Call ID")
        if callid and st.button("Fetch Transcript"):
            with st.spinner("Fetching transcript..."):
                try:
                    trans = did_client._get("/api/cp/transcribe", params={"callid": callid, "_limit": 500}, timeout=30)
                    if not trans:
                        st.warning("No transcript found for this Call ID.")
                    else:
                        segments = sorted(trans, key=lambda x: x.get("dt", ""))
                        st.metric("Segments", len(segments))
                        for t in segments:
                            leg_tag = "CALLER" if str(t.get("leg")) == "1" else "AGENT"
                            st.code(f"[{t.get('dt', '?')}] ({leg_tag}) {t.get('text', '')}")
                        try:
                            trace = did_client._get("/api/cp/log/trace", params={"callid": callid}, timeout=20)
                            from_user = ""
                            for entry in trace if isinstance(trace, list) else []:
                                if entry.get("method") == "INVITE":
                                    from_user = entry.get("from_user", "")
                                    break
                            if from_user:
                                st.info(f"Caller CLI: {from_user}")
                        except Exception:
                            pass
                except Exception as ex:
                    st.error(f"Error: {ex}")
