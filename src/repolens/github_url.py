from __future__ import annotations

import re
from urllib.parse import urlparse

from .errors import InputValidationError
from .models import RepositoryRef

_OWNER_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9-]{1,39}(?<!-)$")
_REPOSITORY_PATTERN = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$")
_ALLOWED_SUBPATHS = {
    "actions",
    "blob",
    "branches",
    "commit",
    "commits",
    "compare",
    "discussions",
    "issues",
    "network",
    "pull",
    "pulls",
    "releases",
    "security",
    "tags",
    "tree",
    "wiki",
}
_RESERVED_OWNERS = {
    "about",
    "collections",
    "contact",
    "customer-stories",
    "enterprise",
    "events",
    "features",
    "login",
    "marketplace",
    "new",
    "notifications",
    "organizations",
    "orgs",
    "pricing",
    "search",
    "security",
    "settings",
    "signup",
    "sponsors",
    "topics",
}


def _invalid(message: str, value: str) -> InputValidationError:
    return InputValidationError(message, details={"input_length": len(value)})


def parse_github_repository_url(value: str) -> RepositoryRef:
    """Parse a public GitHub repository URL into owner and repository name."""
    normalized = value.strip()
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _invalid("GitHub URL 端口不合法", value) from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.hostname.lower() not in {"github.com", "www.github.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise _invalid("请输入有效的 GitHub 仓库 URL", value)

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise _invalid("URL 中缺少仓库所有者或仓库名", value)
    if len(parts) > 2 and parts[2].lower() not in _ALLOWED_SUBPATHS:
        raise _invalid("URL 包含无法识别的 GitHub 仓库子路径", value)

    owner, name = parts[0], parts[1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    if owner.lower() in _RESERVED_OWNERS or not _OWNER_PATTERN.fullmatch(owner):
        raise _invalid("GitHub 仓库所有者名称不合法", value)
    if not _REPOSITORY_PATTERN.fullmatch(name):
        raise _invalid("GitHub 仓库名称不合法", value)
    return RepositoryRef(owner=owner, name=name)

