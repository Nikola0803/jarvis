"""GitHub integration: repos, open issues and pull requests.

Read-only. Powers the HUD PROJECTS panel (via /api/projects) and the MCP tools
ZERO calls conversationally. Auth is a personal access token kept in
/etc/hermes/config.yaml (never in git). Every call is best-effort: on error it
raises a short RuntimeError the caller turns into a friendly message.
"""
from __future__ import annotations

import httpx

API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get(token: str, path: str, params: dict | None = None):
    url = path if path.startswith("http") else f"{API}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers=_headers(token), params=params or {})
    if r.status_code == 401:
        raise RuntimeError("GitHub token rejected (401). Check the token in config.")
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub {r.status_code}: {r.text[:160]}")
    return r.json()


async def list_repos(token: str, limit: int = 8, repos: list[str] | None = None) -> list[dict]:
    """Repos the token can see, most recently pushed first. If `repos` is given
    (a list of 'owner/name'), only those are returned, in that order."""
    if repos:
        out: list[dict] = []
        for full in repos:
            try:
                repo = await _get(token, f"/repos/{full}")
            except RuntimeError:
                continue
            out.append(_repo_row(repo))
        return out[:limit]

    data = await _get(token, "/user/repos", {
        "sort": "pushed",
        "per_page": max(1, min(limit, 50)),
        "affiliation": "owner,collaborator,organization_member",
    })
    return [_repo_row(r) for r in data][:limit]


def _repo_row(repo: dict) -> dict:
    return {
        "name": repo.get("full_name"),
        "short": (repo.get("name") or ""),
        "description": repo.get("description") or "",
        # open_issues_count includes PRs; it's a good "things open" glance number.
        "open_issues": repo.get("open_issues_count", 0),
        "pushed_at": repo.get("pushed_at"),
        "private": repo.get("private", False),
        "url": repo.get("html_url"),
        "default_branch": repo.get("default_branch"),
    }


async def list_open_pull_requests(token: str, repo: str, limit: int = 10) -> list[dict]:
    """Open PRs for one 'owner/name' repo."""
    data = await _get(token, f"/repos/{repo}/pulls", {"state": "open", "per_page": limit})
    return [
        {
            "number": p.get("number"),
            "title": p.get("title"),
            "author": (p.get("user") or {}).get("login"),
            "draft": p.get("draft", False),
            "url": p.get("html_url"),
            "updated_at": p.get("updated_at"),
        }
        for p in data
    ]


async def list_open_issues(token: str, repo: str, limit: int = 10) -> list[dict]:
    """Open issues for one 'owner/name' repo (pull requests filtered out)."""
    data = await _get(token, f"/repos/{repo}/issues", {"state": "open", "per_page": limit})
    issues = [i for i in data if "pull_request" not in i]
    return [
        {
            "number": i.get("number"),
            "title": i.get("title"),
            "author": (i.get("user") or {}).get("login"),
            "labels": [l.get("name") for l in (i.get("labels") or [])],
            "url": i.get("html_url"),
            "updated_at": i.get("updated_at"),
        }
        for i in issues
    ]


async def projects_overview(cfg) -> list[dict]:
    """The PROJECTS panel payload: recent repos with their open-work count."""
    return await list_repos(cfg.token, limit=cfg.max_shown, repos=cfg.repos or None)
