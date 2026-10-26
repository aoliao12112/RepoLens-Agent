from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import IssueTheme, RepositoryTreeSignals

_DEPENDENCY_FILES = {
    "cargo.toml",
    "composer.json",
    "environment.yml",
    "gemfile",
    "go.mod",
    "package-lock.json",
    "package.json",
    "pipfile",
    "poetry.lock",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
}
_ENTRYPOINT_FILES = {
    "app.py",
    "cli.py",
    "index.js",
    "index.ts",
    "main.go",
    "main.py",
    "manage.py",
    "server.py",
}
_ISSUE_THEMES = {
    "installation": {"dependency", "install", "setup", "安装", "依赖"},
    "runtime_error": {"bug", "crash", "error", "exception", "fail", "报错", "崩溃"},
    "compatibility": {"compatibility", "incompatible", "version", "兼容", "版本"},
    "documentation": {"docs", "documentation", "example", "文档", "示例"},
    "performance": {"memory", "performance", "slow", "性能", "内存", "缓慢"},
}


def classify_tree_paths(paths: Iterable[str], *, truncated: bool) -> RepositoryTreeSignals:
    normalized = sorted({path.strip("/") for path in paths if path.strip("/")})

    def file_name(path: str) -> str:
        return path.rsplit("/", 1)[-1].lower()

    def segments(path: str) -> list[str]:
        return [part.lower() for part in path.split("/")]

    dependency_files = [path for path in normalized if file_name(path) in _DEPENDENCY_FILES]
    test_paths = [
        path
        for path in normalized
        if any(part in {"test", "tests", "spec", "specs"} for part in segments(path))
        or file_name(path).startswith(("test_", "spec_"))
    ]
    ci_files = [
        path
        for path in normalized
        if path.lower().startswith(".github/workflows/")
        or file_name(path) in {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile"}
    ]
    docker_files = [
        path
        for path in normalized
        if file_name(path).startswith("dockerfile")
        or file_name(path) in {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}
    ]
    documentation_paths = [
        path
        for path in normalized
        if segments(path)[0] in {"doc", "docs", "documentation"}
        or file_name(path).startswith("readme")
    ]
    example_paths = [
        path
        for path in normalized
        if any(part in {"demo", "demos", "example", "examples"} for part in segments(path))
    ]
    entrypoint_files = [path for path in normalized if file_name(path) in _ENTRYPOINT_FILES]
    return RepositoryTreeSignals(
        paths=normalized,
        dependency_files=dependency_files,
        test_paths=test_paths,
        ci_files=ci_files,
        docker_files=docker_files,
        documentation_paths=documentation_paths,
        example_paths=example_paths,
        entrypoint_files=entrypoint_files,
        truncated=truncated,
    )


def summarize_issue_themes(issues: Iterable[Mapping[str, Any]]) -> list[IssueTheme]:
    matches: dict[str, list[str]] = {name: [] for name in _ISSUE_THEMES}
    for issue in issues:
        content = f"{issue.get('title') or ''} {issue.get('body') or ''}".lower()
        url = issue.get("html_url")
        for name, keywords in _ISSUE_THEMES.items():
            if any(keyword in content for keyword in keywords) and isinstance(url, str):
                matches[name].append(url)

    themes = [
        IssueTheme(name=name, count=len(urls), sample_urls=urls[:5])
        for name, urls in matches.items()
        if urls
    ]
    return sorted(themes, key=lambda theme: (-theme.count, theme.name))
