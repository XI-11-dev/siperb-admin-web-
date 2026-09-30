import base64
import httpx
from config import BASE_URL, ENCODED_PAT

MAX_PAGES = 500

class ApiError(Exception):
    def __init__(self, message, status=None, text=None):
        super().__init__(message)
        self.status = status
        self.text = text

class SiperbClient:
    def __init__(self):
        self.owner_user_id = None
        self.headers = None
        self._http = httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_keepalive_connections=100, max_connections=200))

    async def login(self):
        pat = base64.b64decode(ENCODED_PAT).decode()
        r = await self._http.post(
            f"{BASE_URL}/Login",
            headers={"Authorization": f"Bearer {pat}"}
        )
        if not r.is_success:
            raise ApiError("Login failed. Check PAT.", r.status_code, r.text)
        data = r.json()
        self.owner_user_id = data["UserId"]
        self.headers = {
            "X-Api-Key": data["SessionToken"],
            "Content-Type": "application/json"
        }
        return self

    async def request(self, method, path, json=None, ok_statuses=None):
        if ok_statuses is None:
            ok_statuses = range(200, 300)
        url = f"{BASE_URL}{path}"
        r = await self._http.request(method, url, headers=self.headers, json=json)
        if r.status_code not in ok_statuses:
            raise ApiError("API request failed", r.status_code, r.text)
        if r.text:
            try:
                return r.json()
            except Exception:
                return r.text
        return None

    async def get_paginated(self, path, params=None):
        """GET a list endpoint, following X-Next-Token until exhausted.

        The API caps list responses at a default page size and signals more
        data via the X-Next-Token response header. Callers get the full list,
        so they never silently operate on a truncated first page.
        """
        url = f"{BASE_URL}{path}"
        results = []
        next_token = None
        seen_tokens = set()
        for _ in range(MAX_PAGES):
            query = dict(params or {})
            if next_token:
                query["nextToken"] = next_token
            r = await self._http.request("GET", url, headers=self.headers, params=query or None)
            if not r.is_success:
                raise ApiError("API request failed", r.status_code, r.text)
            if r.text:
                try:
                    page = r.json()
                except Exception:
                    break
                if not isinstance(page, list):
                    return page
                results.extend(page)
            next_token = r.headers.get("X-Next-Token")
            if not next_token or next_token in seen_tokens:
                return results
            seen_tokens.add(next_token)
        return results

    async def get_owner_profile(self):
        return await self.request("GET", f"/Users/{self.owner_user_id}")

    async def get_users(self):
        return await self.get_paginated(f"/Users/{self.owner_user_id}/DomainUsers")

    async def find_user(self, email, users_cache=None):
        users = users_cache if users_cache is not None else await self.get_users()
        for user in users:
            if user.get("UserEmail", "").lower() == email.lower():
                return user
        return None

    async def list_connections(self, domain_user_id):
        return await self.get_paginated(f"/Users/{self.owner_user_id}/DomainUsers/{domain_user_id}/Connections")

    async def get_connection(self, domain_user_id, connection_id):
        return await self.request("GET", f"/Users/{self.owner_user_id}/DomainUsers/{domain_user_id}/Connections/{connection_id}")

    async def create_connection(self, domain_user_id, name, conn_type):
        return await self.request("POST", f"/Users/{self.owner_user_id}/DomainUsers/{domain_user_id}/Connections",
                                  json={"Name": name, "Type": conn_type})

    async def update_connection(self, domain_user_id, connection_id, payload):
        return await self.request("PUT", f"/Users/{self.owner_user_id}/DomainUsers/{domain_user_id}/Connections/{connection_id}",
                                  json=payload)

    async def delete_connection(self, domain_user_id, connection_id):
        return await self.request("DELETE", f"/Users/{self.owner_user_id}/DomainUsers/{domain_user_id}/Connections/{connection_id}")

    async def delete_domain_user(self, domain_user_id):
        return await self.request("DELETE", f"/Users/{self.owner_user_id}/DomainUsers/{domain_user_id}?send_exit_email=no")

    async def close(self):
        await self._http.aclose()
