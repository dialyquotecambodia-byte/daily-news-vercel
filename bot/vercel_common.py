"""Shared helpers for the Vercel serverless functions (api/*).

Cron / secret-guard rules:
- Vercel Cron sends `Authorization: Bearer <CRON_SECRET>` on every scheduled
  request, where CRON_SECRET is the secret Vercel auto-generates for the
  project once a cron is configured (or one you set yourself in the dashboard).
- The /api/post-news endpoint uses the same secret: the VM research cron
  calls it with `Authorization: Bearer <CRON_SECRET>`.
- If CRON_SECRET is not set at all (local development), the check is skipped
  with a warning printed to the log. In production on Vercel it is always
  set, so the check is always enforced there.
"""
import os


def cron_authorized(headers):
    """True when the request carries the Vercel cron / shared secret."""
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        print("WARNING: CRON_SECRET is not set, skipping auth check (local dev only)", flush=True)
        return True
    auth = headers.get("Authorization") or ""
    return auth == f"Bearer {secret}"
