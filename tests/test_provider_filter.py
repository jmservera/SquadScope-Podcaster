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


def test_known_benign_azure_annotation_shapes_are_accepted():
    response = _response()
    response["choices"][0]["content_filter_results"] = {
        "hate": {"filtered": False, "severity": "safe"},
        "custom_blocklists": {
            "filtered": False,
            "details": [{"filtered": False, "id": "list-1"}],
        },
        "jailbreak": {"filtered": False, "detected": False},
        "protected_material_text": {"filtered": False, "detected": False},
        "protected_material_code": {
            "filtered": False,
            "detected": False,
            "citation": {"URL": "https://example.com", "license": "MIT"},
        },
        "ungrounded_material": {"filtered": False, "detected": False, "details": []},
    }
    validate_chat_filter_response(response)
    response["choices"][0]["content_filter_results"] = {
        "custom_blocklists": [{"id": "list-1", "filtered": False}]
    }
    validate_chat_filter_response(response)


@pytest.mark.parametrize(
    "annotation",
    [
        {"custom_blocklists": {"filtered": False, "details": [{"filtered": True, "id": "x"}]}},
        {"custom_blocklists": [{"id": "x", "filtered": True}]},
        {"custom_blocklists": {"filtered": False, "details": [{"error": {"code": "x"}}]}},
        {"jailbreak": {"detected": False, "surprise": {"filtered": True}}},
        {"future_category": {"filtered": True}},
        {"future_category": {"detected": "maybe"}},
        {"future_category": {"details": [{"error": {"code": "x"}}]}},
        {"protected_material_code": {"filtered": False, "citation": "bad"}},
        {
            "protected_material_code": {
                "filtered": False,
                "citation": {"error": "content_filter_error"},
            }
        },
        {"protected_material_code": {"filtered": False, "citation": {"URL": 1}}},
        {"hate": {"filtered": False, "details": "bad"}},
    ],
)
def test_unlisted_or_filtered_nested_shapes_fail_closed(annotation):
    response = _response()
    response["choices"][0]["content_filter_results"] = annotation
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(response)


def test_empty_choices_fail_closed_uniformly():
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response({"choices": []})
    with pytest.raises(FilterSafetyError):
        extract_claims(
            article_content="Content here.",
            article_url="https://example.com",
            config=_config(),
            token_provider=lambda scope: "t",
            transport=_transport({"choices": []}),
        )


@pytest.mark.parametrize("category", ["hate", "sexual", "violence", "self_harm"])
@pytest.mark.parametrize("location", ["prompt", "completion"])
@pytest.mark.parametrize(
    "annotation", [{"severity": "high", "detected": False}, {"severity": "safe"}]
)
def test_severity_categories_require_filtered_bool(category, location, annotation):
    results = {category: annotation}
    if location == "prompt":
        extra = {"prompt_filter_results": [{"prompt_index": 0, "content_filter_results": results}]}
        response = _response(extra)
    else:
        response = _response()
        response["choices"][0]["content_filter_results"] = results
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(response)


def test_severity_category_with_filtered_false_passes():
    response = _response()
    response["choices"][0]["content_filter_results"] = {
        "hate": {"filtered": False, "severity": "safe"},
        "jailbreak": {"detected": False, "filtered": False},
    }
    validate_chat_filter_response(response)


def _at(location, results):
    if location == "prompt":
        return _response(
            {"prompt_filter_results": [{"prompt_index": 0, "content_filter_results": results}]}
        )
    response = _response()
    response["choices"][0]["content_filter_results"] = results
    return response


@pytest.mark.parametrize("location", ["prompt", "completion"])
@pytest.mark.parametrize(
    "annotation",
    [
        {"detected": False},
        {"id": "x"},
        {"filtered": "no"},
        {"filtered": True},
        {"filtered": False, "details": [{"id": "x"}]},
        {"filtered": False, "details": [{"filtered": True, "id": "x"}]},
        {},
        [],
        [{"detected": False}],
        [{"filtered": False, "details": [{"id": "x"}]}],
    ],
)
def test_custom_blocklists_require_filtered_decision(location, annotation):
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(_at(location, {"custom_blocklists": annotation}))


@pytest.mark.parametrize("location", ["prompt", "completion"])
def test_custom_blocklists_dict_and_list_with_decision_pass(location):
    entry = {"filtered": False, "details": [{"filtered": False, "id": "x"}]}
    validate_chat_filter_response(_at(location, {"custom_blocklists": entry}))
    validate_chat_filter_response(_at(location, {"custom_blocklists": [entry]}))


@pytest.mark.parametrize("location", ["prompt", "completion"])
@pytest.mark.parametrize(
    "category",
    [
        "profanity",
        "jailbreak",
        "indirect_attack",
        "protected_material_text",
        "protected_material_code",
        "ungrounded_material",
    ],
)
@pytest.mark.parametrize(
    "annotation", [{}, {"severity": "safe"}, {"id": "x"}, {"detected": "false"}]
)
def test_detection_categories_require_boolean_decision(location, category, annotation):
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(_at(location, {category: annotation}))


@pytest.mark.parametrize("key", ["prompt_filter_results", "prompt_annotations"])
@pytest.mark.parametrize(
    "value",
    [
        [{"prompt_index": 0}],
        [{"prompt_index": True, "content_filter_results": {"hate": {"filtered": False}}}],
        [{"content_filter_results": {"hate": {"severity": "safe"}}}],
        {},
    ],
)
def test_prompt_results_shapes_fail_closed(key, value):
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(_response({key: value}))


@pytest.mark.parametrize(
    "offsets", [{}, [], {"check_offset": "0"}, {"check_offset": -1}, {"error": 1}]
)
def test_malformed_content_filter_offsets_fail_closed(offsets):
    response = _response()
    response["choices"][0]["content_filter_offsets"] = offsets
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(response)


def test_valid_content_filter_offsets_pass():
    response = _response()
    response["choices"][0]["content_filter_offsets"] = {
        "check_offset": 0,
        "start_offset": 0,
        "end_offset": 5,
    }
    validate_chat_filter_response(response)


def _default_v2_gpt4o_response():
    # Shape modelled on Azure OpenAI gpt-4o 2024-11-20 / DefaultV2 (content scrubbed).
    clean = {"filtered": False, "severity": "safe"}
    flags = {"filtered": False, "detected": False}
    return {
        "id": "chatcmpl-SCRUBBED",
        "object": "chat.completion",
        "model": "gpt-4o-2024-11-20",
        "system_fingerprint": "fp_scrubbed",
        "prompt_filter_results": [
            {
                "prompt_index": 0,
                "content_filter_results": {
                    "hate": clean,
                    "jailbreak": flags,
                    "self_harm": clean,
                    "sexual": clean,
                    "violence": clean,
                    "custom_topics": {"filtered": False, "detected": False, "details": []},
                    "pii": {**flags, "redacted": False},
                },
            }
        ],
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "logprobs": None,
                "message": {"role": "assistant", "content": "scrubbed", "refusal": None},
                "content_filter_results": {
                    "hate": clean,
                    "protected_material_code": flags,
                    "protected_material_text": flags,
                    "self_harm": clean,
                    "sexual": clean,
                    "violence": clean,
                    "task_adherence": {**flags, "reasoning": "scrubbed"},
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_default_v2_unknown_non_decision_fields_and_empty_results_pass():
    response = _default_v2_gpt4o_response()
    validate_chat_filter_response(response)
    response["choices"][0]["content_filter_results"] = {}
    response["prompt_filter_results"][0]["content_filter_results"] = {}
    validate_chat_filter_response(response)


@pytest.mark.parametrize(
    "extra",
    [
        {"filtered": True},
        {"detected": True},
        {"detected": None},
        {"error": {"code": "x"}},
        {"details": [{"filtered": True}]},
    ],
)
def test_unknown_category_carrying_a_decision_or_error_fails_closed(extra):
    response = _default_v2_gpt4o_response()
    response["choices"][0]["content_filter_results"]["future_category"] = {
        "filtered": False,
        **extra,
    }
    with pytest.raises(FilterSafetyError):
        validate_chat_filter_response(response)


def test_rejection_reason_has_sanitized_path_and_no_content():
    response = _default_v2_gpt4o_response()
    response["choices"][0]["content_filter_results"]["hate sexual!"] = {"filtered": True}
    with pytest.raises(FilterSafetyError) as caught:
        validate_chat_filter_response(response)
    assert caught.value.reason == (
        "chat completion reported filtering at choices.0.content_filter_results.?.filtered"
    )
    response = _default_v2_gpt4o_response()
    response["choices"][0]["content_filter_results"]["violence"] = {
        "filtered": False,
        "severity": "SECRET TEXT",
    }
    with pytest.raises(FilterSafetyError) as caught:
        validate_chat_filter_response(response)
    assert "SECRET" not in caught.value.reason
    assert caught.value.reason.endswith("violence.severity")
