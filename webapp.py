import asyncio
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone, timedelta

import streamlit as st

from client import SiperbClient, ApiError
import config
from connexcs_did import ConnexCSClient

st.set_page_config(page_title="Eleven Solutions LLC", page_icon="favicon.jpeg", layout="wide")

# ── Theme override ────────────────────────────────────────────
st.markdown("""
<style>
.stApp, #root > div:first-child > div:first-child { background: #fff !important; }
.stSidebar, .css-1d391kg, .css-1lcbmhc, section[data-testid="stSidebar"] { background: #f5f5f5 !important; }
h1, h2, h3, h4 { color: #FF6B00 !important; }
.stMarkdown, .stText, p, li, label, .stRadio > div, .stRadio label { color: #000 !important; }
.stButton > button { background: #FF6B00 !important; color: #fff !important; border: none !important; font-weight: bold !important; }
.stButton > button:hover { background: #FF8C33 !important; }
.stButton > button:active { background: #E65C00 !important; }
.stTextInput > div > div, .stTextArea > div > div, .stSelectbox > div > div, .stMultiSelect > div > div, .stNumberInput > div > div { background: #fff !important; border: 1px solid #ccc !important; color: #000 !important; }
.stTextInput input, .stTextArea textarea, .stNumberInput input { color: #000 !important; }
.css-1m6wrjf, .css-16txtl3, .stMetric { background: #f9f9f9 !important; border: 1px solid #e0e0e0 !important; border-radius: 8px !important; padding: 12px !important; }
.stMetric label { color: #FF6B00 !important; }
.stMetric .css-1xarl3l, .stMetric .css-qrbaxs { color: #000 !important; }
.stDataFrame { background: #fff !important; color: #000 !important; }
.stDataFrame table { color: #000 !important; }
div[data-testid="stExpander"] { background: #f9f9f9 !important; border: 1px solid #e0e0e0 !important; }
.stCode { background: #f5f5f5 !important; color: #FF6B00 !important; }
.stSpinner > div { border-top-color: #FF6B00 !important; }
hr { border-color: #e0e0e0 !important; }
.stSuccess { background: #d4edda !important; color: #155724 !important; }
.stError { background: #f8d7da !important; color: #721c24 !important; }
.stWarning { background: #fff3cd !important; color: #856404 !important; }
.stInfo { background: #e2f0fb !important; border: 1px solid #b3d7ff !important; color: #004085 !important; }
</style>
""", unsafe_allow_html=True)

# ── Login gate ──────────────────────────────────────────────
if "_auth" not in st.session_state:
    st.session_state._auth = False

if not st.session_state._auth:
    st.title("Eleven Solutions LLC")
    st.markdown("### Login")
    pwd = st.text_input("Password", type="password")
    if st.button("Login", type="primary"):
        if pwd == "0308":
            st.session_state._auth = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# ── Persistent event loop ────────────────────────────────────
if "_loop" not in st.session_state:
    st.session_state._loop = asyncio.new_event_loop()
loop = st.session_state._loop
asyncio.set_event_loop(loop)

def run(coro):
    return loop.run_until_complete(coro)

def parse_emails(raw):
    """Support comma-separated and/or newline-separated emails."""
    raw = raw.replace(",", "\n")
    return [l.strip() for l in raw.strip().splitlines() if l.strip()]

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
async def find_user(client, email, users=None):
    if users is None:
        users = await client.get_users()
    for u in users:
        if u.get("UserEmail", "").lower() == email.lower():
            return u
    return None

async def list_connections(client, uid):
    return await client.request("GET", f"/Users/{client.owner_user_id}/DomainUsers/{uid}/Connections")

async def extend_one(client, email, days, users=None):
    user = await find_user(client, email, users)
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
            details.append(f"{name}: {'+' if days >= 0 else ''}{days}d")
        except ApiError as e:
            details.append(f"{name}: failed ({e.status})")
            result = "warning"
    return (email, result, "; ".join(details))

async def set_expiry_date(client, email, target_date, users=None):
    user = await find_user(client, email, users)
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
            old_dt = datetime.fromisoformat(old.replace("Z", "+00:00"))
            target = target_date.replace(tzinfo=timezone.utc)
            delta = (target - old_dt).total_seconds() / 86400
            new_dt = old_dt + timedelta(days=delta)
            detail["Expiry"]["End"] = new_dt.isoformat().replace("+00:00", "Z")
            await client.update_connection(uid, cid, detail)
            details.append(f"{name}: set to {target_date.strftime('%b %d, %Y')}")
        except ApiError as e:
            details.append(f"{name}: failed ({e.status})")
            result = "warning"
    return (email, result, "; ".join(details))

async def delete_one(client, email, users=None, send_exit=False):
    user = await find_user(client, email, users)
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
        exit_param = "yes" if send_exit else "no"
        await client.request("DELETE", f"{base}?send_exit_email={exit_param}")
    except ApiError as e:
        return (email, "failed", f"delete failed ({e.status})")
    result = "warning" if warnings else "success"
    detail = "; ".join(warnings) if warnings else "deleted"
    if send_exit:
        detail += " (exit email sent)"
    return (email, result, detail)

async def provision_user(client, email, profile_type, caller_id=None, rec=False, vm=False, domain="", extension="", did="", expiry_days=None, send_welcome=False):
    from profiles import build_connexcs_profile, build_pbx_profile, build_connection_payload
    user = await find_user(client, email)
    if user:
        return (email, "failed", "already exists")
    if profile_type == "pbx":
        profile = build_pbx_profile(domain or "", extension or "", did or caller_id or "")
    else:
        profile = build_connexcs_profile(caller_id or email.split("@")[0])
    if expiry_days is not None:
        expiry = datetime.now(timezone.utc) + timedelta(days=expiry_days)
    else:
        from config import TRIAL_HOURS, TRIAL_MINUTES
        expiry = datetime.now(timezone.utc) + timedelta(hours=TRIAL_HOURS, minutes=TRIAL_MINUTES)
    try:
        data = await client.request("POST", f"/Users/{client.owner_user_id}/DomainUsers",
                                    json={"Email": email, "UserType": "Internal",
                                          "Description": profile["description"],
                                          "JoinMailingList": True, "SendWelcomeEmail": send_welcome})
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

async def refresh_one(client, email, users=None):
    user = await find_user(client, email, users)
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

async def change_caller_id_one(client, email, new_cid, users=None):
    user = await find_user(client, email, users)
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
    st.markdown("<h1 style='color:#FF6B00;'>Eleven</h1>", unsafe_allow_html=True)
    st.markdown("---")
    pages = [
        "Dashboard", "Create User", "Extend Expiry", "Delete User",
        "Edit Connection", "Refresh Connection", "Change Caller ID",
        "Audit", "Send Notifications", "ConnexCS DID",
    ]
    page = st.radio("", pages, key="nav", label_visibility="collapsed")
    st.markdown("---")
    st.caption("v6.4 Web")

# ══════════════════════════════════════════════════════════════
if page == "Dashboard":
    st.title("Dashboard")
    if not config.RESEND_API_KEY:
        st.warning("RESEND_API_KEY not set in Streamlit Secrets — Send Notifications won't work.")
    if not config.CONNEXCS_USERNAME or not config.CONNEXCS_PASSWORD:
        st.warning("ConnexCS credentials not set in Streamlit Secrets — ConnexCS DID won't work.")
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
    search_q = st.text_input("Filter by email", placeholder="Leave empty for all users")
    if st.button("Run Audit", type="primary"):
        async def run_audit():
            client = get_client()
            users = await client.get_users()
            if search_q.strip():
                q = search_q.strip().lower()
                users = [u for u in users if q in u.get("UserEmail", "").lower()]
            total = len(users)
            cutoff = datetime.now(timezone.utc) + timedelta(days=config.EXPIRING_SOON_DAYS)
            from audit import parse_date, days_left
            results = {"no_expiry": [], "expired": [], "soon": [], "healthy": [], "high_conc": [], "done": 0}
            status_text = st.empty()
            pbar = st.progress(0, text="Auditing...")
            async def process_user(u):
                email = u.get("UserEmail", "")
                uid = u.get("UserEmailId")
                status_text.text(f"Processing {email}...")
                try:
                    conns = await client.list_connections(uid)
                except Exception:
                    return [("no_expiry", (email, "", "fetch failed", 1))]
                if not conns:
                    return [("no_expiry", (email, "", "no connections", 1))]
                async def resolve(c):
                    name = c.get("Name", "")
                    cid = c.get("ConnectionId")
                    try:
                        detail = await client.get_connection(uid, cid)
                    except Exception:
                        return ("no_expiry", (email, name, "detail fetch failed", 1))
                    end = detail.get("Expiry", {}).get("End")
                    dt = parse_date(end) if end else None
                    conc = detail.get("OutboundCallConcurrency")
                    try:
                        conc = int(conc) if conc is not None else 1
                    except (ValueError, TypeError):
                        conc = 1
                    if dt is None:
                        return ("no_expiry", (email, name, cid, conc))
                    elif days_left(dt) < 0:
                        return ("expired", (email, name, cid, dt, conc))
                    elif dt <= cutoff:
                        return ("soon", (email, name, cid, dt, conc))
                    return ("healthy", (email, name, cid, dt, conc))
                return await asyncio.gather(*[resolve(c) for c in conns])
            tasks = [process_user(u) for u in users]
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results["done"] += 1
                for tag, data in result if isinstance(result, list) else [result]:
                    if tag == "no_expiry" and data:
                        results["no_expiry"].append(data)
                    elif tag == "expired":
                        results["expired"].append(data)
                    elif tag == "soon":
                        results["soon"].append(data)
                    elif tag == "healthy" and data:
                        results["healthy"].append(data)
                        conc = data[-1]
                        if conc > 1:
                            results["high_conc"].append(data)
                pbar.progress(results["done"] / total)
            status_text.empty()
            pbar.empty()
            return total, results
        total, results = run(run_audit())
        results["expired"].sort(key=lambda x: x[3])
        results["soon"].sort(key=lambda x: x[3])
        results["healthy"].sort(key=lambda x: x[3])
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total", total)
        col2.metric("Expired", len(results["expired"]))
        col3.metric("Expiring Soon", len(results["soon"]))
        col4.metric("Healthy", len(results["healthy"]))
        col5.metric("High Concurrency", len(results["high_conc"]))
        if results["no_expiry"]:
            with st.expander(f"Missing Expiry ({len(results['no_expiry'])})", expanded=True):
                for e, n, r, _ in results["no_expiry"]:
                    st.warning(f"**{e}** — {n} ({r})")
        def fmt_expiry(dt):
            now = datetime.now(timezone.utc)
            sec = int((dt - now).total_seconds())
            expired = sec < 0
            sec = abs(sec)
            d = sec // 86400
            h = (sec % 86400) // 3600
            m = (sec % 3600) // 60
            if expired:
                return f"EXPIRED {d}d {h}h"
            if d > 0:
                return f"{d}d {h}h"
            return f"{h}h {m}m"
        if results["high_conc"]:
            with st.expander(f"High Concurrency ({len(results['high_conc'])})", expanded=True):
                st.dataframe(
                    [{"Email": e, "Connection": n, "Concurrency": c} for e, n, _, _, c in results["high_conc"]],
                    use_container_width=True, hide_index=True
                )
        if results["expired"]:
            with st.expander(f"Expired ({len(results['expired'])})", expanded=True):
                st.dataframe(
                    [{"Email": e, "Connection": n, "Status": fmt_expiry(d)} for e, n, _, d, _ in results["expired"]],
                    use_container_width=True, hide_index=True
                )
        if results["soon"]:
            with st.expander(f"Expiring Soon ({len(results['soon'])})", expanded=True):
                rows = []
                for e, n, _, d, _ in results["soon"]:
                    rows.append({"Email": e, "Connection": n, "Remaining": fmt_expiry(d)})
                st.dataframe(rows, use_container_width=True, hide_index=True)
        if search_q.strip() and results["healthy"]:
            with st.expander(f"Healthy ({len(results['healthy'])})", expanded=False):
                st.dataframe(
                    [{"Email": e, "Connection": n, "Remaining": fmt_expiry(d)} for e, n, _, d, _ in results["healthy"]],
                    use_container_width=True, hide_index=True
                )
        is_filtered = search_q.strip()
        all_clear = not any([results["no_expiry"], results["expired"], results["soon"]])
        if all_clear and not is_filtered:
            st.success("All connections have valid expiry. No issues.")

# ══════════════════════════════════════════════════════════════
elif page == "Create User":
    st.title("Create User")
    typ = st.selectbox("Profile Type", ["pbx", "connexcs"])
    mode = st.radio("Mode", ["Single", "Bulk"], horizontal=True)
    is_pbx = typ == "pbx"
    pbx_domain = st.text_input("PBX Domain", placeholder="pbx.example.com") if is_pbx else ""
    if mode == "Single":
        emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com", height=68)
        caller_id = st.text_input("Caller ID" if not is_pbx else "DID", placeholder="Required")
        pbx_ext = st.text_input("Extension", placeholder="1000") if is_pbx else ""
    else:
        if is_pbx:
            emails_raw = st.text_area("Rows: email, extension, did (one per line)", placeholder="user@example.com,1000,5551234", height=100)
        else:
            emails_raw = st.text_area("Rows: email, callerid (one per line)", placeholder="user@example.com,5551234", height=100)
        caller_id = ""
        pbx_ext = ""
    rec = st.checkbox("Enable call recording")
    vm = st.checkbox("Enable voicemail")
    acc_type = st.radio("Account type", ["Trial", "Paid"], horizontal=True)
    paid_days = st.number_input("Paid duration (days)", value=30, min_value=1) if acc_type == "Paid" else 0
    send_welcome = st.checkbox("Send welcome email", value=False)
    if st.button("Create", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        elif mode == "Single" and not caller_id.strip():
            st.warning("Caller ID / DID is required.")
        elif is_pbx and mode == "Single" and not pbx_ext.strip():
            st.warning("Extension is required.")
        elif is_pbx and not pbx_domain.strip():
            st.warning("PBX Domain is required.")
        else:
            client = get_client()
            lines = [l.strip() for l in emails_raw.strip().splitlines() if l.strip()]
            expiry_days = paid_days if acc_type == "Paid" else None
            results = []
            with st.spinner("Creating..."):
                async def run_create():
                    tasks = []
                    for line in lines:
                        if is_pbx and mode == "Bulk":
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) < 3:
                                st.warning(f"Skipped invalid: {line}")
                                continue
                            email, ext, did = parts[0], parts[1], parts[2]
                            tasks.append(provision_user(client, email, typ, did, rec, vm, domain=pbx_domain, extension=ext, did=did, expiry_days=expiry_days, send_welcome=send_welcome))
                        elif is_pbx and mode == "Single":
                            tasks.append(provision_user(client, line, typ, caller_id, rec, vm, domain=pbx_domain, extension=pbx_ext, did=caller_id, expiry_days=expiry_days, send_welcome=send_welcome))
                        elif mode == "Single":
                            tasks.append(provision_user(client, line, typ, caller_id, rec, vm, expiry_days=expiry_days, send_welcome=send_welcome))
                        else:
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) < 2:
                                st.warning(f"Skipped invalid: {line}")
                                continue
                            tasks.append(provision_user(client, parts[0], typ, parts[1], rec, vm, expiry_days=expiry_days, send_welcome=send_welcome))
                    for coro in asyncio.as_completed(tasks):
                        results.append(await coro)
                run(run_create())
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
    st.title("Extend / Set Connection Expiry")
    mode = st.radio("Mode", ["Extend by days", "Set expiry date"], horizontal=True)
    emails_raw = st.text_area("Email(s) — one per line", placeholder="user@example.com")
    if mode == "Extend by days":
        days = st.number_input("Days (positive to add, negative to subtract)", value=30)
    else:
        target_date = st.date_input("Set expiry date")
    confirm = st.checkbox("I confirm I want to modify expiry for the above users")
    if st.button("Apply", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        elif not confirm:
            st.warning("Please confirm the action.")
        else:
            client = get_client()
            lines = parse_emails(emails_raw)
            results = []
            spinner_text = "Extending..." if mode == "Extend by days" else "Setting expiry..."
            with st.spinner(spinner_text):
                async def run_extend():
                    all_users = await client.get_users()
                    tasks = []
                    for email in lines:
                        if mode == "Extend by days":
                            tasks.append(extend_one(client, email, days, all_users))
                        else:
                            tasks.append(set_expiry_date(client, email, target_date, all_users))
                    for coro in asyncio.as_completed(tasks):
                        results.append(await coro)
                run(run_extend())
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
    send_exit = st.checkbox("Send exit email to user", value=False)
    confirm = st.checkbox("I confirm I want to delete the above users permanently")
    if st.button("Delete", type="primary"):
        if not emails_raw.strip():
            st.warning("Enter at least one email.")
        elif not confirm:
            st.warning("Please confirm the action.")
        else:
            client = get_client()
            lines = parse_emails(emails_raw)
            results = []
            with st.spinner("Deleting..."):
                async def run_del():
                    all_users = await client.get_users()
                    tasks = [delete_one(client, email, all_users, send_exit) for email in lines]
                    for coro in asyncio.as_completed(tasks):
                        results.append(await coro)
                run(run_del())
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
elif page == "Edit Connection":
    st.title("Edit Connection")
    client = get_client()
    email = st.text_input("User email", placeholder="user@example.com")
    if st.button("Load Connections", type="primary"):
        if not email.strip():
            st.warning("Enter an email.")
        else:
            with st.spinner("Fetching connections..."):
                async def fetch_conns():
                    all_users = await client.get_users()
                    user = await client.find_user(email.strip(), all_users)
                    if not user:
                        return None, [], "User not found"
                    uid = user["UserId"]
                    conns = await client.list_connections(uid)
                    return uid, conns, None
                uid, conns, err = run(fetch_conns())
            if err:
                st.error(err)
            elif not conns:
                st.info("No connections for this user.")
            else:
                st.session_state._ec_uid = uid
                st.session_state._ec_conns = conns
                st.session_state.ec_detail = None
                st.session_state._ec_cid = None
                st.rerun()

    if st.session_state.get("_ec_uid"):
        uid = st.session_state._ec_uid
        conns = st.session_state._ec_conns
        co = {f"{c.get('Name','?')} ({c.get('ConnectionId','')[:8]}...)" : c for c in conns}
        sel_name = st.selectbox("Select connection", list(co.keys()), key="ec_sel")
        conn = co[sel_name]
        cid = conn["ConnectionId"]

        if not st.session_state.get("ec_detail") or st.session_state.get("_ec_cid") != cid:
            with st.spinner("Fetching details..."):
                async def fetch_detail():
                    return await client.get_connection(uid, cid)
                st.session_state.ec_detail = run(fetch_detail())
                st.session_state._ec_cid = cid

        detail = st.session_state.ec_detail
        if detail:
            cur_type = detail.get("Type", "")
            cur_conc = detail.get("OutboundCallConcurrency", "")
            new_type = st.text_input("Type", value=cur_type, key="ec_type")
            try:
                cur_conc_val = int(cur_conc) if cur_conc is not None else 1
            except (ValueError, TypeError):
                cur_conc_val = 1
            new_conc = st.number_input("OutboundCallConcurrency", value=cur_conc_val, min_value=1, step=1, key="ec_conc")
            if st.button("Update Connection", type="primary", use_container_width=True):
                detail["Type"] = new_type
                detail["OutboundCallConcurrency"] = new_conc
                try:
                    run(client.update_connection(uid, cid, detail))
                    st.success(f"Connection {cid[:8]}... updated")
                    st.session_state.ec_detail = None
                    st.session_state._ec_cid = None
                    st.rerun()
                except Exception as ex:
                    st.error(f"Error: {ex}")
            if st.button("Clear", use_container_width=True):
                st.session_state._ec_uid = None
                st.session_state._ec_conns = None
                st.session_state.ec_detail = None
                st.session_state._ec_cid = None
                st.rerun()

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
            lines = parse_emails(emails_raw)
            results = []
            with st.spinner("Refreshing..."):
                async def run_ref():
                    all_users = await client.get_users()
                    tasks = [refresh_one(client, email, all_users) for email in lines]
                    for coro in asyncio.as_completed(tasks):
                        results.append(await coro)
                run(run_ref())
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
                    results = []
                    with st.spinner("Updating..."):
                        async def run_cid():
                            all_users = await client.get_users()
                            tasks = [change_caller_id_one(client, e, c, all_users) for e, c in rows]
                            for coro in asyncio.as_completed(tasks):
                                results.append(await coro)
                        run(run_cid())
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
elif page == "Send Notifications":
    st.title("Send Due Date Notifications")
    if config.RESEND_API_KEY and config.SENDER_EMAIL:
        st.success("Resend API key and sender email configured in Streamlit Secrets.")
        api_key = config.RESEND_API_KEY
        sender = config.SENDER_EMAIL
    else:
        api_key = st.text_input("Resend API key", type="password")
        sender = st.text_input("Sender email", value="billing@elev1solutions.com")
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
                confirm_send = st.checkbox("I confirm I want to send reminders to the above recipients", key="notify_confirm")
                if confirm_send and st.button("2. Send Reminders", type="primary"):
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
        confirm_listed = st.checkbox("I confirm I want to send reminders to the listed emails", key="notify_listed_confirm")
        if confirm_listed and st.button("Send to Listed Emails", type="primary"):
            if not emails_raw.strip():
                st.warning("Enter at least one email.")
            elif not api_key:
                st.warning("Enter your Resend API key.")
            else:
                client = get_client()
                with st.spinner("Sending..."):
                    lines = parse_emails(emails_raw)
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
    st.title("DID Inventory")
    did_client = ConnexCSClient()

    for k in ["_did_data", "_did_filter", "_assigning", "_bulk_assign", "_bulk_ids", "_selected_dids"]:
        if k not in st.session_state:
            if k == "_did_filter":
                st.session_state[k] = "All"
            elif k in ("_selected_dids",):
                st.session_state[k] = set()
            else:
                st.session_state[k] = None

    def refresh_dids():
        with st.spinner("Fetching DIDs..."):
            try:
                all_dids = did_client.fetch_all_dids()
                for d in all_dids:
                    if isinstance(d.get("tags"), str):
                        d["tags"] = [t.strip() for t in d["tags"].split(",") if t.strip()]
                    elif not isinstance(d.get("tags"), list):
                        d["tags"] = []
                st.session_state._did_data = all_dids
            except Exception as ex:
                st.error(f"Error: {ex}")

    if st.session_state._did_data is None:
        refresh_dids()

    dids = st.session_state._did_data or []
    cnt_un = sum(1 for d in dids if not d.get("customer_id"))
    cnt_as = len(dids) - cnt_un
    flt = st.session_state._did_filter

    # Header + Refresh
    col_h1, col_h2 = st.columns([4, 1])
    with col_h1:
        st.markdown("### DID Inventory")
    with col_h2:
        if st.button("⟳ Refresh Inventory", use_container_width=True):
            refresh_dids(); st.rerun()

    # Metric cards
    m1, m2, m3 = st.columns(3)
    m1.metric("Total DIDs", len(dids))
    m2.metric("Unassigned (Inventory)", cnt_un)
    m3.metric("Assigned", cnt_as)

    # Filter pills
    col_ft1, col_ft2, col_ft3, col_count = st.columns([1, 1.5, 1.5, 3])
    with col_ft1:
        if st.button("All DIDs", type="primary" if flt == "All" else "secondary", use_container_width=True):
            st.session_state._did_filter = "All"; st.rerun()
    with col_ft2:
        if st.button("Unassigned", type="primary" if flt == "Unassigned" else "secondary", use_container_width=True):
            st.session_state._did_filter = "Unassigned"; st.rerun()
    with col_ft3:
        if st.button("Assigned", type="primary" if flt == "Assigned" else "secondary", use_container_width=True):
            st.session_state._did_filter = "Assigned"; st.rerun()
    with col_count:
        display = [d for d in dids if
                   (flt == "All") or
                   (flt == "Unassigned" and not d.get("customer_id")) or
                   (flt == "Assigned" and d.get("customer_id"))]
        st.caption(f"Showing {len(display)} numbers")

    # ── Manage Tags + Transcript (side by side) ──
    if dids:
        col_left, col_right = st.columns(2)
        with col_left:
            with st.expander("Manage Tags", expanded=False):
                did_opts = {f"{d['did']}" + (" [Assigned]" if d.get("customer_id") else " [Unassigned]"): d for d in dids}
                sel = st.selectbox("Select DID", list(did_opts.keys()), key="mt_did")
                sel_did = did_opts[sel]
                st.write(f"**Current tags:** {', '.join(sel_did.get('tags') or []) or '(none)'}")
                col_t1, col_t2 = st.columns([1, 1])
                new_t = col_t1.text_input("Add tag", key="mt_add", label_visibility="collapsed", placeholder="New tag")
                if col_t1.button("Add", key="mt_add_btn", use_container_width=True):
                    if new_t:
                        full = did_client.get_did(sel_did["id"])
                        ex = full.get("tags") or []
                        if new_t not in ex:
                            ex.append(new_t)
                            full["tags"] = ex
                            did_client.update_did(sel_did["id"], full)
                        refresh_dids(); st.rerun()
                if col_t2.button("Clear all tags", key="mt_clr", use_container_width=True):
                    full = did_client.get_did(sel_did["id"])
                    full["tags"] = []
                    did_client.update_did(sel_did["id"], full)
                    refresh_dids(); st.rerun()
                with st.expander("Remove individual tags", expanded=False):
                    tags = sel_did.get("tags") or []
                    if tags:
                        for t in tags:
                            if st.button(f"✗ {t}", key=f"mt_rm_{sel_did['id']}_{t}"):
                                full = did_client.get_did(sel_did["id"])
                                full["tags"] = [x for x in (full.get("tags") or []) if x != t]
                                did_client.update_did(sel_did["id"], full)
                                refresh_dids(); st.rerun()
                    else:
                        st.write("No tags to remove.")
        with col_right:
            with st.expander("Pull Call Transcript", expanded=False):
                callid = st.text_input("Call ID", key="did_ts_id")
                if callid and st.button("Fetch", key="did_ts_fetch"):
                    with st.spinner("Fetching..."):
                        try:
                            trans = did_client._get("/api/cp/transcribe", params={"callid": callid, "_limit": 500}, timeout=30)
                            if not trans:
                                st.warning("No transcript found.")
                            else:
                                segments = sorted(trans, key=lambda x: x.get("dt", ""))
                                st.metric("Segments", len(segments))
                                for t in segments:
                                    leg = "CALLER" if str(t.get("leg")) == "1" else "AGENT"
                                    st.code(f"[{t.get('dt', '?')}] ({leg}) {t.get('text', '')}")
                                try:
                                    trace = did_client._get("/api/cp/log/trace", params={"callid": callid}, timeout=20)
                                    for entry in trace if isinstance(trace, list) else []:
                                        if entry.get("method") == "INVITE":
                                            fu = entry.get("from_user", "")
                                            if fu:
                                                st.info(f"CLI: {fu}")
                                                try:
                                                    dd = did_client._get("/api/cp/did", params={"did": fu, "_limit": 5}, timeout=15)
                                                    for dd2 in dd:
                                                        tt = dd2.get("tags", [])
                                                        if tt:
                                                            st.info(f"Tags: [{', '.join(tt)}]")
                                                except Exception:
                                                    pass
                                            break
                                except Exception:
                                    pass
                        except Exception as ex:
                            st.error(f"Error: {ex}")

    # ── Unified table ──
    if not dids:
        st.info("No DIDs loaded. Click Refresh.")
    else:
        st.markdown("""
        <style>
        .did-badge { display: inline-block; padding: 1px 8px; border-radius: 9999px; font-size: 12px; font-weight: 500; }
        </style>
        """, unsafe_allow_html=True)

        search_q = st.text_input("Search DIDs", placeholder="Filter by DID number...", label_visibility="collapsed")
        if search_q:
            sq = search_q.strip().lower()
            display = [d for d in display if sq in d["did"].lower()]

        sel = st.session_state._selected_dids

        # ── Bulk actions bar (top) ──
        if sel:
            bc1, bc2, bc3 = st.columns([1.5, 1.5, 1.5])
            bc1.markdown(f"**{len(sel)} selected**")
            if bc2.button("Bulk Unassign", use_container_width=True):
                for sid in list(sel):
                    full = did_client.get_did(sid)
                    full["customer_id"] = None
                    did_client.update_did(sid, full)
                sel.clear()
                refresh_dids(); st.rerun()
            if bc3.button("Bulk Assign", use_container_width=True):
                st.session_state._bulk_ids = list(sel)
                st.session_state._bulk_assign = True
                st.rerun()

        h_cols = st.columns([0.4, 2, 1.2, 2, 1.4])
        h_cols[0].markdown("**✓**")
        h_cols[1].markdown("**DID**")
        h_cols[2].markdown("**Status**")
        h_cols[3].markdown("**Tags**")
        h_cols[4].markdown("**Actions**")

        for d in display:
            did_id = d["id"]
            is_as = bool(d.get("customer_id"))
            tags = d.get("tags") or []
            cols = st.columns([0.4, 2, 1.2, 2, 1.4])
            if cols[0].checkbox("", value=did_id in sel, key=f"sel_{did_id}", label_visibility="collapsed"):
                sel.add(did_id)
            else:
                sel.discard(did_id)
            cols[1].write(d["did"])
            bg = "#d1fae5;color:#065f46" if is_as else "#fef3c7;color:#92400e"
            cols[2].markdown(f"<span class='did-badge' style='background:{bg}'>{'Assigned' if is_as else 'Unassigned'}</span>", unsafe_allow_html=True)
            cols[3].write(", ".join(tags) if tags else "-")
            if is_as:
                if cols[4].button("Unassign", key=f"u_{did_id}", use_container_width=True):
                    full = did_client.get_did(did_id)
                    full["customer_id"] = None
                    did_client.update_did(did_id, full)
                    refresh_dids(); st.rerun()
            else:
                if cols[4].button("Assign", key=f"a_{did_id}", use_container_width=True):
                    st.session_state._assigning = did_id; st.rerun()

        # ── Assign form ──
        if st.session_state.get("_assigning"):
            did_id = st.session_state._assigning
            d = next((x for x in dids if x["id"] == did_id), None)
            if d:
                st.markdown(f"**Assign {d['did']}**")
                try:
                    customers = did_client.get_customers()
                    if not customers:
                        st.error("No customers.")
                        st.session_state._assigning = None
                    else:
                        co = {f"{c.get('name') or c.get('company_name') or c.get('email','(no name)')}": c for c in customers}
                        cust = st.selectbox("Customer", list(co.keys()), key="ac_cust")
                        customer = co[cust]
                        ips = did_client.get_customer_ips(customer["id"])
                        hosts = sorted(set((r.get("fqdn") or r.get("ip") or "").strip() for r in ips if r.get("fqdn") or r.get("ip")))
                        ho = hosts + ["Custom"]
                        dl = st.selectbox("Destination", ho, key="ac_dest")
                        dh = st.text_input("IP/host", key="ac_dh") if dl == "Custom" else dl
                        nt = st.text_input("Tags to add", key="ac_tags", placeholder="comma-separated")
                        if st.button("Confirm Assign", key="ac_confirm", use_container_width=True):
                            if not dh:
                                st.warning("Destination required.")
                            else:
                                full = did_client.get_did(did_id)
                                full["customer_id"] = customer["id"]
                                full["destination"] = f"{d['did']}@{dh}"
                                full["destination_type"] = "uri"
                                if nt:
                                    for t in [x.strip() for x in nt.split(",") if x.strip()]:
                                        ex = full.get("tags") or []
                                        if t not in ex:
                                            ex.append(t)
                                        full["tags"] = ex
                                did_client.update_did(did_id, full)
                                st.success(f"{d['did']} assigned")
                                st.session_state._assigning = None
                                refresh_dids(); st.rerun()
                        if st.button("Cancel", key="ac_cancel"):
                            st.session_state._assigning = None; st.rerun()
                except Exception as ex:
                    st.error(f"Error: {ex}")

        # ── Bulk assign form ──
        if st.session_state.get("_bulk_assign"):
            bulk_ids = st.session_state._bulk_ids
            st.markdown(f"**Bulk Assign ({len(bulk_ids)} DIDs)**")
            try:
                customers = did_client.get_customers()
                if not customers:
                    st.error("No customers.")
                    st.session_state._bulk_assign = False
                else:
                    co = {f"{c.get('name') or c.get('company_name') or c.get('email','(no name)')}": c for c in customers}
                    cust = st.selectbox("Customer", list(co.keys()), key="ba_cust")
                    customer = co[cust]
                    ips = did_client.get_customer_ips(customer["id"])
                    hosts = sorted(set((r.get("fqdn") or r.get("ip") or "").strip() for r in ips if r.get("fqdn") or r.get("ip")))
                    ho = hosts + ["Custom"]
                    dl = st.selectbox("Destination", ho, key="ba_dest")
                    dh = st.text_input("IP/host", key="ba_dh") if dl == "Custom" else dl
                    nt = st.text_input("Tags to add", key="ba_tags", placeholder="comma-separated")
                    if st.button("Confirm Bulk Assign", key="ba_confirm", use_container_width=True):
                        if not dh:
                            st.warning("Destination required.")
                        else:
                            for bid in bulk_ids:
                                full = did_client.get_did(bid)
                                full["customer_id"] = customer["id"]
                                d_num = next((x["did"] for x in dids if x["id"] == bid), bid)
                                full["destination"] = f"{d_num}@{dh}"
                                full["destination_type"] = "uri"
                                if nt:
                                    for t in [x.strip() for x in nt.split(",") if x.strip()]:
                                        ex = full.get("tags") or []
                                        if t not in ex:
                                            ex.append(t)
                                        full["tags"] = ex
                                did_client.update_did(bid, full)
                            st.success(f"{len(bulk_ids)} DIDs assigned")
                            st.session_state._bulk_assign = False
                            st.session_state._bulk_ids = None
                            st.session_state._selected_dids.clear()
                            refresh_dids(); st.rerun()
                    if st.button("Cancel", key="ba_cancel"):
                        st.session_state._bulk_assign = False
                        st.session_state._bulk_ids = None
                        st.rerun()
            except Exception as ex:
                st.error(f"Error: {ex}")
