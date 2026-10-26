from repolens.repository_signals import classify_tree_paths, summarize_issue_themes


def test_classify_tree_paths_finds_engineering_signals() -> None:
    result = classify_tree_paths(
        [
            "pyproject.toml",
            "src/app.py",
            "tests/test_app.py",
            ".github/workflows/ci.yml",
            "Dockerfile",
            "docs/guide.md",
            "examples/basic.py",
            "README.md",
        ],
        truncated=True,
    )

    assert result.dependency_files == ["pyproject.toml"]
    assert result.test_paths == ["tests/test_app.py"]
    assert result.ci_files == [".github/workflows/ci.yml"]
    assert result.docker_files == ["Dockerfile"]
    assert result.documentation_paths == ["README.md", "docs/guide.md"]
    assert result.example_paths == ["examples/basic.py"]
    assert result.entrypoint_files == ["src/app.py"]
    assert result.truncated is True


def test_summarize_issue_themes_counts_and_limits_samples() -> None:
    issues = [
        {
            "title": "Install error on Windows",
            "body": "Dependency setup fails",
            "html_url": f"https://github.com/owner/repo/issues/{number}",
        }
        for number in range(1, 8)
    ]

    themes = summarize_issue_themes(issues)

    assert [theme.name for theme in themes[:2]] == ["installation", "runtime_error"]
    assert themes[0].count == 7
    assert len(themes[0].sample_urls) == 5
