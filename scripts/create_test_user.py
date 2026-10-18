"""Create (or reset) an email/password Firebase user so evaluators without a Google account can sign in.

Usage: python scripts/create_test_user.py evaluator@example.com 'a-strong-password'
Needs gcloud credentials with Firebase Authentication admin on GCP_PROJECT_ID, and the Email/Password sign-in provider
enabled (Firebase console > Authentication > Sign-in method). The user is created with a verified email, which the API
requires. If ALLOWED_EMAIL_DOMAIN is set on the API, the address must be in that domain. Share the credentials with
evaluators out of band; they are not stored in the repository.
"""

from __future__ import annotations

import os
import subprocess
import sys

import requests


def main(email: str, password: str) -> None:
    project = os.environ["GCP_PROJECT_ID"]
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    base = f"https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts"
    headers = {"Authorization": f"Bearer {token}", "X-Goog-User-Project": project}
    found = requests.post(f"{base}:lookup", headers=headers, json={"email": [email]}, timeout=30).json()
    if found.get("users"):
        body = {"localId": found["users"][0]["localId"], "password": password, "emailVerified": True}
        response = requests.post(f"{base}:update", headers=headers, json=body, timeout=30)
    else:
        response = requests.post(base, headers=headers, json={"email": email, "password": password, "emailVerified": True}, timeout=30)
    response.raise_for_status()
    print(f"Test user ready: {email}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
