import pytest

from repolens.errors import ErrorCode, InputValidationError
from repolens.github_url import parse_github_repository_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/openai/openai-agents-python", "openai/openai-agents-python"),
        ("https://www.github.com/langchain-ai/langgraph.git", "langchain-ai/langgraph"),
        ("https://github.com/lllyasviel/IC-Light/issues", "lllyasviel/IC-Light"),
        ("https://github.com/openai/openai-agents-python/tree/main?tab=readme", "openai/openai-agents-python"),
        ("  HTTP://GITHUB.COM/OpenAI/openai-agents-python.GIT  ", "OpenAI/openai-agents-python"),
    ],
)
def test_parse_valid_repository_urls(url: str, expected: str) -> None:
    assert parse_github_repository_url(url).full_name == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/a/b",
        "https://github.com/openai",
        "not-a-url",
        "https://github.com.evil.example/openai/repo",
        "https://user@github.com/openai/repo",
        "https://github.com:abc/openai/repo",
        "https://github.com/-invalid/repo",
        "https://github.com/openai/bad%2Frepo",
        "https://github.com/openai/repo/unknown-section",
        "https://github.com/settings/profile",
    ],
)
def test_reject_invalid_repository_urls(url: str) -> None:
    with pytest.raises(InputValidationError) as exc_info:
        parse_github_repository_url(url)

    assert exc_info.value.code == ErrorCode.invalid_input
    assert exc_info.value.retryable is False

