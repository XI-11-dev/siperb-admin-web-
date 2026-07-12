import requests
from cli import ask, yes_no, section, print_table, green, yellow, red

CONNEXCS_BASE = "https://app.connexcs.com"
USERNAME = "business@elevensolutions.info"
PASSWORD = "Office@11"


class ConnexCSClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.auth = (USERNAME, PASSWORD)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _get(self, path, params=None, timeout=30):
        resp = self.session.get(f"{CONNEXCS_BASE}{path}", params=params, timeout=timeout)
        if resp.status_code == 401:
            raise SystemExit("ConnexCS authentication failed.")
        resp.raise_for_status()
        return resp.json()

    def _put(self, path, data, timeout=30):
        resp = self.session.put(f"{CONNEXCS_BASE}{path}", json=data, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def get_dids(self, limit=1000, offset=0):
        return self._get("/api/cp/did", params={"_limit": limit, "_offset": offset})

    def get_did(self, did_id):
        return self._get(f"/api/cp/did/{did_id}")

    def update_did(self, did_id, data):
        return self._put(f"/api/cp/did/{did_id}", data=data)

    def get_customers(self, limit=200):
        return self._get("/api/cp/customer", params={"_limit": limit, "status": "Active"})

    def get_customer_ips(self, customer_id):
        return self._get("/api/cp/switch/ip", params={"company_id": customer_id, "_limit": 50})

    def fetch_all_unassigned(self):
        all_dids = []
        offset = 0
        while True:
            data = self.get_dids(limit=1000, offset=offset)
            if not data:
                break
            all_dids.extend(data)
            if len(data) < 1000:
                break
            offset += 1000
        return [d for d in all_dids if not d.get("customer_id")]

    def fetch_all_dids(self, page_size=1000):
        all_dids = []
        offset = 0
        while True:
            data = self.get_dids(limit=page_size, offset=offset)
            if not data:
                break
            all_dids.extend(data)
            if len(data) < page_size:
                break
            offset += page_size
        return all_dids


def parse_range(raw, max_val):
    raw = raw.strip().lower()
    if raw == "all":
        return None
    indices = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                for x in range(int(a.strip()), int(b.strip()) + 1):
                    indices.add(x)
            except ValueError:
                pass
        else:
            try:
                indices.add(int(part))
            except ValueError:
                pass
    selected = []
    for i in sorted(indices):
        if 1 <= i <= max_val:
            selected.append(i)
    return selected if selected else None


def flow_inventory(client):
    section("DID INVENTORY")
    dids = client.fetch_all_unassigned()
    unassigned = [d for d in dids if not d.get("customer_id")]
    assigned = [d for d in dids if d.get("customer_id")]
    print(f"  Total: {len(dids)} DIDs")
    print(f"  Unassigned (inventory): {len(unassigned)}")
    print(f"  Assigned: {len(assigned)}")
    if unassigned:
        print()
        for i, d in enumerate(unassigned, 1):
            tags = ", ".join(d["tags"]) if isinstance(d.get("tags"), list) else str(d.get("tags") or "")
            print(f"  {i:3d}. {d['did']}  [{tags}]")
    input("\nPress Enter to continue...")


def flow_assign(client):
    section("ASSIGN DIDS")
    unassigned = client.fetch_all_unassigned()
    if not unassigned:
        print("No unassigned DIDs found.")
        input("Press Enter to continue...")
        return

    print(f"\nUnassigned DIDs ({len(unassigned)}):\n")
    for i, d in enumerate(unassigned, 1):
        tags = ", ".join(d["tags"]) if isinstance(d.get("tags"), list) else str(d.get("tags") or "")
        print(f"  {i:3d}. {d['did']}  [{tags}]")

    print(f"\nSelect DIDs (examples: 1-5, 10, 15-20 or 'all'):")
    raw = input("Enter: ").strip()
    if raw.lower() == "all":
        selected = unassigned
    else:
        idxs = parse_range(raw, len(unassigned))
        if not idxs:
            print("No valid DIDs selected.")
            return
        selected = [unassigned[i - 1] for i in idxs]

    print(f"  Selected {len(selected)} DID(s)")

    customers = client.get_customers()
    if not customers:
        print("No active customers found.")
        return

    print(f"\nActive Customers:\n")
    for i, c in enumerate(customers, 1):
        name = c.get("name") or c.get("company_name") or c.get("email") or "(no name)"
        print(f"  {i:3d}. {name}  (id={c['id']})")

    while True:
        try:
            choice = int(input("\nPick customer: "))
            if 1 <= choice <= len(customers):
                customer = customers[choice - 1]
                break
        except ValueError:
            pass
        print(f"Pick 1-{len(customers)}")

    ips = client.get_customer_ips(customer["id"])
    hosts = sorted(set(
        (r.get("fqdn") or r.get("ip") or "").strip()
        for r in ips if r.get("fqdn") or r.get("ip")
    ))

    print(f"\n  Customer: {customer.get('name')} (id={customer['id']})")
    if hosts:
        print(f"\n  IP Auth destinations:\n")
        for i, host in enumerate(hosts, 1):
            print(f"  {i:3d}. {host}")
        print(f"  {len(hosts)+1}. Enter custom IP/host")
        while True:
            try:
                choice = int(input("\nPick destination: "))
                if 1 <= choice <= len(hosts):
                    dest_host = hosts[choice - 1]
                    break
                elif choice == len(hosts) + 1:
                    dest_host = input("Enter IP/host: ").strip()
                    if dest_host:
                        break
                print(f"Pick 1-{len(hosts)+1}")
            except ValueError:
                print(f"Pick 1-{len(hosts)+1}")
    else:
        print("\n  No IP Auth found.")
        dest_host = input("  Enter IP/host: ").strip()
        if not dest_host:
            print("Destination required.")
            return

    tag_input = input("\nEnter tags to add (comma-separated, or Enter to skip): ").strip()
    new_tags = [t.strip() for t in tag_input.split(",") if t.strip()] if tag_input else []

    print(f"\nAssigning {len(selected)} DID(s)...\n")
    for i, did in enumerate(selected, 1):
        destination = f"{did['did']}@{dest_host}"
        full = client.get_did(did["id"])
        full["customer_id"] = customer["id"]
        full["destination"] = destination
        full["destination_type"] = "uri"
        if new_tags:
            existing = full.get("tags") or []
            merged = existing[:]
            for t in new_tags:
                if t not in merged:
                    merged.append(t)
            full["tags"] = merged
        client.update_did(did["id"], full)
        print(f"  {i}. {did['did']} -> {destination}")
    print(f"\nDone! {len(selected)} DID(s) assigned.")
    input("Press Enter to continue...")


def flow_unassign(client):
    section("UNASSIGN DIDS")
    raw = input("Enter DIDs to unassign (comma-separated): ").strip()
    if not raw:
        return
    search = [n.strip() for n in raw.split(",") if n.strip()]
    print(f"  Searching for {len(search)} number(s)...\n")
    all_dids = client.fetch_all_dids()
    found = [d for d in all_dids if d["did"] in search]
    if not found:
        print("No matching DIDs found.")
        return
    not_found = [n for n in search if n not in [d["did"] for d in found]]
    if not_found:
        print(f"  Not found: {', '.join(not_found)}")
    to_unassign = []
    for d in found:
        if d.get("customer_id"):
            to_unassign.append(d)
            print(f"  {d['did']}  -> assigned to customer_id={d['customer_id']}")
        else:
            print(f"  {d['did']}  -> already unassigned (skipping)")
    if not to_unassign:
        print("\nNothing to unassign.")
        return
    if not yes_no(f"\nUnassign {len(to_unassign)} DID(s)? (yes/no): "):
        print("Cancelled.")
        return
    print()
    for d in to_unassign:
        full = client.get_did(d["id"])
        full["customer_id"] = None
        client.update_did(d["id"], full)
        print(f"  {d['did']}  -> returned to inventory")
    print(f"\nDone! {len(to_unassign)} DID(s) unassigned.")
    input("Press Enter to continue...")


def show_call_context(client, callid, trans):
    cid = trans[0].get("customer_id")
    print(f"\n--- CALL INFO ---")
    print(f"  Call ID: {callid}")
    try:
        trace = client._get("/api/cp/log/trace", params={"callid": callid}, timeout=20)
        from_user = ""
        for entry in trace if isinstance(trace, list) else []:
            if entry.get("method") == "INVITE":
                from_user = entry.get("from_user", "")
                break
        if from_user:
            print(f"  Caller CLI: {from_user}")
            try:
                dids = client._get("/api/cp/did", params={"did": from_user, "_limit": 5}, timeout=15)
                if dids:
                    for d in dids:
                        tags = d.get("tags", [])
                        if tags:
                            print(f"  Tags: [{', '.join(tags)}]")
            except Exception:
                pass
    except Exception:
        pass


def show_transcript(trans, callid):
    segments = sorted(trans, key=lambda x: x.get("dt", ""))
    print(f"\nTranscript ({len(segments)} segments):")
    for t in segments:
        leg_tag = "CALLER" if str(t.get("leg")) == "1" else "AGENT"
        print(f"  [{t.get('dt', '?')}] ({leg_tag}) {t.get('text', '')}")


def flow_transcript(client):
    section("PULL CALL TRANSCRIPT")
    callid = input("Enter Call ID: ").strip()
    if not callid:
        return
    trans = client._get("/api/cp/transcribe", params={"callid": callid, "_limit": 500}, timeout=30)
    if not trans:
        print("No transcript found for this Call ID.")
        input("Press Enter to continue...")
        return
    show_call_context(client, callid, trans)
    show_transcript(trans, callid)
    input("Press Enter to continue...")
