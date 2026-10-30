"""Proves the CVAT editor sign-in against Cognito end to end, without a browser and without printing credentials:
portal editor-launch URL -> CVAT OIDC -> Cognito hosted UI (form login) -> CVAT callback -> CVAT session for the linked
account, which can read its assigned job and not an unassigned one.
Needs: stack up with WITH_COGNITO=1, portal API on :8000, seed_poc.py run, a job assigned to the test user.
  set -a; . deploy/.env; set +a; uv run python scripts/verify_cvat_signin.py"""
import os
import re
import sys
from html import unescape
from urllib.parse import urljoin

import httpx

MARKETPLACE = os.environ.get("MARKETPLACE_API_URL", "https://api.unspace.cassyork.com") + "/api/v1"
PORTAL = os.environ.get("LABELING_API_URL", "http://localhost:8000") + "/api/v1/labeling"
CVAT = os.environ.get("CVAT_URL", "http://localhost:8080")


def main() -> int:
    email, pw = os.environ["LABELING_TEST_EMAIL"], os.environ["LABELING_TEST_PASSWORD"]
    token = httpx.post(f"{MARKETPLACE}/auth/login", json={"email": email, "password": pw}).json()["tokens"]["accessToken"]
    api = {"authorization": f"Bearer {token}"}
    jobs = httpx.get(f"{PORTAL}/jobs", headers=api, params={"assignee": "me"}).json()["items"]
    launch = httpx.post(f"{PORTAL}/jobs/{jobs[0]['id']}/editor-launch", headers=api).json()["url"]
    print("1 editor-launch url:", launch.split("?")[0])
    own_job = int(re.search(r"jobs%2F(\d+)", launch).group(1))

    c = httpx.Client(follow_redirects=False, timeout=30)
    url, hops = launch, 0
    while hops < 15:
        r = c.get(url)
        hops += 1
        if r.is_redirect:
            url = urljoin(str(r.url), r.headers["location"])
            continue
        if "amazoncognito.com" in str(r.url) and "<form" in r.text and "password" in r.text:
            form = next(f for f in re.finditer(r'<form[^>]*action="([^"]*)"[^>]*>(.*?)</form>', r.text, re.S) if 'name="password"' in f.group(2))
            data = {m.group(1): unescape(m.group(2)) for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form.group(2)) if m.group(1) not in ("username", "password")}
            data.update(username=email, password=pw)
            print("2 Cognito hosted UI form reached; submitting credentials")
            r = c.post(urljoin(str(r.url), unescape(form.group(1))), data=data)
            if r.is_redirect:
                url = urljoin(str(r.url), r.headers["location"])
                continue
            print("   hosted UI did not redirect after login:", r.status_code)
            return 1
        break
    print("3 landed on:", str(r.url).split("?")[0], r.status_code)

    me = c.get(f"{CVAT}/api/users/self", headers={"accept": "application/json"})
    if me.status_code != 200:
        print("4 CVAT session NOT established:", me.status_code)
        return 1
    print("4 CVAT session user:", me.json()["username"])
    visible = {j["id"] for j in c.get(f"{CVAT}/api/jobs", params={"page_size": 100}, headers={"accept": "application/json"}).json()["results"]}
    print(f"5 jobs this CVAT user can see: {sorted(visible)} (expect only the assigned job {own_job})")
    return 0 if visible == {own_job} else 1


if __name__ == "__main__":
    sys.exit(main())
