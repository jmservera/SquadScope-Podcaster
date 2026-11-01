"""Offline contract checks for reported Azure chat-completion filtering state."""

import json
from urllib.error import HTTPError

import pytest

from podcaster.claim_extraction import extract_claims
from podcaster.hooks import generate_hooks
from podcaster.provider_filter import FilterSafetyError, validate_chat_filter_response
from podcaster.script_gen import ScriptGenConfig, _request_dialogue


def _config():
    return ScriptGenConfig(
        endpoint="https://example.invalid/",
        chat_deployment="chat",
        auth_mode="managed_identity",
    )


def _response(annotation=None, *, finish="stop"):
    response = {"choices": [{"finish_reason": finish, "message": {"content": '{"claims": []}'}}]}
    if annotation is not None:
        response.update(annotation)
    return response


def _transport(response):
    return lambda request: json.dumps(response).encode()


@pytest.mark.parametrize(
    "annotation",
    [
        {
            "prompt_filter_results": [
                {
                    "content_filter_results": {
                        "jailbreak": {"error": {"code": "content_filter_error"}}
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "unsafe"},
                    "content_filter_results": {"hate": {"error": {"code": "unknown"}}},
                }
            ]
        },
        {"prompt_filter_results": [{"content_filter_results": {"error": None}}]},
        {"prompt_filter_results": [{"content_filter_results": None}]},
        {"content_filter_results": {"hate": {"error": {"code": "unknown"}}}},
        {
            "prompt_filter_results": [
                {
                    "content_filter_results": {
                        "hate": {"innererror": {"details": [{"provider_error": "unknown"}]}}
                    }
                }
            ]
        },
        {"prompt_filter_results": {"unexpected": "shape"}},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "unsafe"},
                    "content_filter_results": {"hate": {"filtered": True}},
                }
            ]
        },
        {"choices": [{"finish_reason": "content_filter", "message": {"content": "unsafe"}}]},
        {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "unsafe"}},
                {"finish_reason": "content_filter", "message": {"content": "unsafe"}},
            ]
        },
    ],
)
def test_script_rejects_reported_filter_failures(annotation):
    response = _response()
    response.update(annotation)
    with pytest.raises(FilterSafetyError):
        _request_dialogue(
            [{"role": "user", "content": "test"}],
            url="https://example.invalid/chat",
            token="fake",
            transport=_transport(response),
        )


def test_script_allows_clean_optional_or_absent_annotations():
    for annotation in (
        None,
        {
            "prompt_filter_results": [
                {"content_filter_results": {"hate": {"filtered": False, "severity": "safe"}}}
            ],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                    "content_filter_results": {"hate": {"filtered": False}},
                }
            ],
        },
    ):
        response = _response(annotation)
        assert _request_dialogue(
            [{"role": "user", "content": "test"}],
            url="https://example.invalid/chat",
            token="fake",
            transport=_transport(response),
        ) in ('{"claims": []}', "ok")


@pytest.mark.parametrize(
    "response",
    [
        _response(
            {
                "prompt_filter_results": [
                    {
                        "content_filter_results": {
                            "jailbreak": {"error": {"code": "content_filter_error"}}
                        }
                    }
                ]
            }
        ),
        _response(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "unsafe"},
                        "content_filter_results": {"hate": {"error": {"code": "unknown"}}},
                    }
                ]
            }
        ),
        _response(finish="content_filter"),
    ],
)
def test_grounding_rejects_reported_filter_failures(response):
    with pytest.raises(FilterSafetyError):
        extract_claims(
            article_content="A complete article about software releases and products.",
            article_url="https://example.invalid/article",
            config=_config(),
            token_provider=lambda scope: "fake",
            transport=_transport(response),
        )


def test_grounding_allows_clean_response():
    assert (
        extract_claims(
            article_content="A complete article about software releases and products.",
            article_url="https://example.invalid/article",
            config=_config(),
            token_provider=lambda scope: "fake",
            transport=_transport(_response()),
        )
        == []
    )


@pytest.mark.parametrize(
    "category",
    [
        None,
        "content_filter_error",
        [],
        {"code": "content_filter_error"},
        {"error_details": {"code": "content_filter_error"}},
        {"status": "failed"},
        {"unknown": {"nested": "ambiguous"}},
        {"filter_error": False},
        {"filtered": "false"},
        {"filtered": True},
        {"detected": True},
        {"severity": None},
        {"severity": "high"},
    ],
)
@pytest.mark.parametrize("location", ["prompt", "completion"])
def test_present_malformed_or_ambiguous_categories_fail_closed(category, location):
    response = _response()
    if location == "prompt":
        response["prompt_filter_results"] = [{"content_filter_results": {"hate": category}}]
    else:
        response["choices"][0]["content_filter_results"] = {"hate": category}
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(response)


@pytest.mark.parametrize(
    "annotation",
    [
        {"prompt_filter_results": [{"content_filter_results": {"hate": None}}]},
        {
            "prompt_filter_results": [
                {"content_filter_results": {"jailbreak": {"code": "content_filter_error"}}}
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "unsafe"},
                    "content_filter_results": {"hate": "error"},
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "unsafe"},
                    "content_filter_results": {"hate": {"error_details": {"code": "unknown"}}},
                }
            ]
        },
        {"prompt_filter_results": [{"content_filter_results": {"hate": {"status": "failed"}}}]},
        {"innererror": {"code": "content_filter_error"}},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "unsafe"},
                    "innererror": {"code": "content_filter_error"},
                }
            ]
        },
    ],
)
@pytest.mark.parametrize("invoke", ["script", "grounding", "hooks"])
def test_consumers_reject_ambiguous_http_200_annotation(annotation, invoke):
    response = _response()
    response.update(annotation)
    with pytest.raises(FilterSafetyError):
        if invoke == "script":
            _request_dialogue(
                [{"role": "user", "content": "test"}],
                url="https://example.invalid/chat",
                token="fake",
                transport=_transport(response),
            )
        elif invoke == "grounding":
            extract_claims(
                article_content="A complete article about software releases and products.",
                article_url="https://example.invalid/article",
                config=_config(),
                token_provider=lambda scope: "fake",
                transport=_transport(response),
            )
        else:
            generate_hooks(
                config=_config(),
                token_provider=lambda scope: "fake",
                transport=_transport(response),
            )


def test_clean_nested_categories_and_missing_optional_annotations_remain_valid():
    for annotation in (
        None,
        {
            "prompt_filter_results": [
                {
                    "prompt_index": 0,
                    "content_filter_results": {
                        "hate": {"filtered": False, "severity": "safe"},
                        "jailbreak": {"detected": False, "filtered": False},
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "ok"},
                    "content_filter_results": {"hate": {"filtered": False}},
                }
            ]
        },
    ):
        validate_chat_filter_response(_response(annotation))


def test_optional_hooks_do_not_mask_reported_filter_error():
    response = _response(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"host_a": ["a"], "host_b": ["b"]}'},
                    "content_filter_results": {"hate": {"error": {"code": "unknown"}}},
                }
            ]
        }
    )
    with pytest.raises(FilterSafetyError):
        generate_hooks(
            config=_config(),
            token_provider=lambda scope: "fake",
            transport=_transport(response),
        )


def test_only_safe_provider_correlation_is_preserved():
    response = _response(
        {
            "id": "unsafe\npayload",
            "prompt_filter_results": [{"content_filter_results": {"error": {"code": "unknown"}}}],
        }
    )
    with pytest.raises(FilterSafetyError) as caught:
        _request_dialogue(
            [{"role": "user", "content": "test"}],
            url="https://example.invalid/chat",
            token="fake",
            transport=_transport(response),
        )
    assert caught.value.request_id is None
    response["id"] = "chatcmpl-123"
    with pytest.raises(FilterSafetyError) as caught:
        _request_dialogue(
            [{"role": "user", "content": "test"}],
            url="https://example.invalid/chat",
            token="fake",
            transport=_transport(response),
        )
    assert caught.value.request_id == "chatcmpl-123"


@pytest.mark.parametrize("invoke", ["script", "grounding", "hooks"])
def test_blocked_http_400_is_terminal(invoke):
    def blocked(request):
        raise HTTPError(request.full_url, 400, "provider response must not be logged", {}, None)

    with pytest.raises(FilterSafetyError, match="HTTP 400"):
        if invoke == "script":
            _request_dialogue(
                [{"role": "user", "content": "test"}],
                url="https://example.invalid/chat",
                token="fake",
                transport=blocked,
            )
        elif invoke == "grounding":
            extract_claims(
                article_content="A complete article about software releases and products.",
                article_url="https://example.invalid/article",
                config=_config(),
                token_provider=lambda scope: "fake",
                transport=blocked,
            )
        else:
            generate_hooks(
                config=_config(),
                token_provider=lambda scope: "fake",
                transport=blocked,
            )
