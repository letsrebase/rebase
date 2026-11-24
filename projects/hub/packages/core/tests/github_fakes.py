"""GitHub as the hub's HTTP seam sees it, for the account link and the cross-check (REB-1166 to
REB-1169): the OAuth token answer, GraphQL (`viewer` for the person's token, `user(login:)` for
the hub's) and the REST reads of a pull request or a commit. Every call is kept, so a test reads
which token read what."""

import json
import re
from typing import Any
from urllib.parse import parse_qs

HUB_TOKEN = "hub-token"
PERSON_TOKEN = "person-token"
GITHUB_ID = 4242
LOGIN = "ada-dev"

_ALIAS = re.compile(r'(m\d+): contributionsCollection\(from: "(\d{4}-\d{2})')


class FakeGithub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []
        self.identity: dict[str, Any] = {"databaseId": GITHUB_ID, "login": LOGIN}
        # Month to counts, as anybody sees them, and the private part only `read:user` adds.
        self.public: dict[str, dict[str, int]] = {}
        self.private: dict[str, dict[str, int]] = {}
        self.languages: list[list[str]] = [["TypeScript", "JavaScript"], ["HCL"]]
        self.granted = "read:user"
        self.graphql_errors = False
        self.rest: dict[str, tuple[int, Any]] = {}
        self._spent: set[str] = set()

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if url.endswith("/login/oauth/access_token"):
            code = parse_qs(body.decode())["code"][0]
            if code in self._spent:
                return 200, json.dumps({"error": "bad_verification_code"}).encode()
            self._spent.add(code)
            answer = {"access_token": PERSON_TOKEN, "token_type": "bearer", "scope": self.granted}
            return 200, json.dumps(answer).encode()
        if url == "https://api.github.com/graphql":
            return self._graphql(headers, json.loads(body))
        if url in self.rest:
            status, answer = self.rest[url]
            return status, json.dumps(answer).encode()
        return 404, b'{"message": "Not Found"}'

    def token_of(self, headers: dict[str, str]) -> str:
        return headers.get("Authorization", "").removeprefix("Bearer ")

    def _graphql(self, headers: dict[str, str], request: dict[str, Any]) -> tuple[int, bytes]:
        if self.graphql_errors:
            return 200, json.dumps({"errors": [{"message": "boom"}]}).encode()
        query: str = request["query"]
        token = self.token_of(headers)
        if "contributionsCollection" not in query:
            if token != PERSON_TOKEN:
                return 401, b"{}"
            return 200, json.dumps({"data": {"viewer": self.identity}}).encode()
        as_viewer = "viewer {" in query
        node: dict[str, Any] = dict(self.identity)
        for alias, month in _ALIAS.findall(query):
            counts = dict(self.public.get(month, {}))
            if as_viewer:
                for key, value in self.private.get(month, {}).items():
                    counts[key] = counts.get(key, 0) + value
            node[alias] = {
                "totalCommitContributions": counts.get("commits", 0),
                "totalPullRequestContributions": counts.get("pull_requests", 0),
                "totalPullRequestReviewContributions": counts.get("reviews", 0),
                "totalIssueContributions": counts.get("issues", 0),
            }
        node["repositories"] = {
            "nodes": [
                {"languages": {"nodes": [{"name": name} for name in names]}}
                for names in self.languages
            ]
        }
        key = "viewer" if as_viewer else "user"
        return 200, json.dumps({"data": {key: node}}).encode()

    def snapshot_tokens(self) -> list[str]:
        """The token each snapshot query was read with, in order."""
        return [
            self.token_of(headers)
            for _method, url, headers, body in self.calls
            if url.endswith("/graphql") and b"contributionsCollection" in body
        ]


def pull(owner: str, repo: str, number: int) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"


def pull_answer(
    author: int, *, merged_by: int | None = None, repo_owner: int = 1
) -> dict[str, Any]:
    return {
        "user": {"id": author},
        "merged_at": "2026-05-02T10:00:00Z" if merged_by is not None else None,
        "merged_by": {"id": merged_by} if merged_by is not None else None,
        "base": {"repo": {"owner": {"id": repo_owner}}},
    }
