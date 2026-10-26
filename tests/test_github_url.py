import pytest

from repolens.github_url import parse_github_repository_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/openai/openai-agents-python", "openai/openai-agents-python"),
        ("https://www.github.com/langchain-ai/langgraph.git", "langchain-ai/langgraph"),
        ("https://github.com/lllyasviel/IC-Light/issues", "lllyasviel/IC-Light"),
    ],
)
def test_parse_valid_repository_urls(url: str, expected: str) -> None:
    assert parse_github_repository_url(url).full_name == expected


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.com/a/b", "https://github.com/openai", "not-a-url"],
)
def test_reject_invalid_repository_urls(url: str) -> None:
    with pytest.raises(ValueError):
        parse_github_repository_url(url)

