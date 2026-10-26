from __future__ import annotations

from urllib.parse import urlparse

from .models import RepositoryRef


def parse_github_repository_url(value: str) -> RepositoryRef:
    """Parse a public GitHub repository URL into owner and repository name."""
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "github.com",
        "www.github.com",
    }:
        raise ValueError("请输入有效的 GitHub 仓库 URL")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("URL 中缺少仓库所有者或仓库名")

    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        raise ValueError("URL 中缺少仓库所有者或仓库名")
    return RepositoryRef(owner=owner, name=name)

