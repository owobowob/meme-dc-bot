import logging
from functools import partial
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from google import genai
import openai

from services.analyzer import (
    FALLBACK_GEMINI_MODELS,
    TagExtractionError,
    extract_meme_tags,
    _load_image_tagger_template,
    analyze_chat_search,
    analyze_image_with_gemini,
)
from services.tag_store import load_known_tags


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_provider(monkeypatch, provider):
    client = MagicMock()
    tags = ["doge", "this_is_fine"]
    if provider == "gemini":
        monkeypatch.setenv("GEMINI_API_KEY", "test")
        monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
        call = client.models.generate_content
        call.return_value = NS(parsed={"tags": tags})
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
        call = client.chat.completions.create
        call.return_value = NS(choices=[NS(message=NS(content='{"tags":["doge","this_is_fine"]}'))])

    assert extract_meme_tags("這很正常", provider, "custom-model") == {"tags": tags}
    assert call.call_args.kwargs["model"] == "custom-model"
    call.side_effect = RuntimeError("secret")
    with pytest.raises(TagExtractionError, match="API 呼叫失敗") as error:
        extract_meme_tags("聊天", provider)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("tags", [
    ["doge"], ["doge", " DOGE "], ["狗", "doge"], ["doge", "stonks", "crying_cat"], ["doge", "meme"]
])
def test_invalid_output(monkeypatch, tags):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
    client.models.generate_content.return_value = NS(parsed={"tags": tags})
    with pytest.raises(TagExtractionError, match="格式錯誤"):
        extract_meme_tags("聊天", "gemini")


def test_gemini_thinking_disabled_and_schema_compatibility(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
    client.models.generate_content.return_value = NS(parsed={"tags": ["doge", "stonks"]})

    extract_meme_tags("測試思考設定", "gemini", model="gemini-3.8-flash")
    call_config = client.models.generate_content.call_args.kwargs["config"]
    # 1. 非 -lite 模型應關閉思考
    assert call_config["thinking_config"] == {"thinking_budget": 0}
    assert call_config["response_mime_type"] == "application/json"

    # 2. 驗證 response_schema 絕不含 additionalProperties 或 additional_properties
    schema = call_config["response_schema"]
    schema_dump = getattr(schema, "model_dump", None)
    schema_repr = str(schema_dump(exclude_none=True) if schema_dump else schema).lower()
    assert "additionalproperties" not in schema_repr
    assert "additional_properties" not in schema_repr


def test_gemini_retry_and_fallback_to_lite(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)

    from google.genai.errors import ServerError
    # 主力模型 503 重試 1 次仍失敗，降級至 gemini-3.5-flash-lite 成功
    call = client.models.generate_content
    call.side_effect = [
        ServerError(503, "Unavailable - high load"),
        ServerError(503, "Unavailable - high load"),
        NS(parsed={"tags": ["doge", "stonks"]}),
    ]

    with caplog.at_level(logging.WARNING):
        result = extract_meme_tags("測試降級至lite", "gemini", model="gemini-3.8-flash")

    assert result == {"tags": ["doge", "stonks"]}
    assert call.call_count == 3
    # 第 1, 2 次是 gemini-3.8-flash
    assert call.call_args_list[0].kwargs["model"] == "gemini-3.8-flash"
    assert call.call_args_list[1].kwargs["model"] == "gemini-3.8-flash"
    assert call.call_args_list[0].kwargs["config"]["thinking_config"] == {"thinking_budget": 0}

    # 第 3 次降級至 gemini-3.5-flash-lite
    lite_call = call.call_args_list[2]
    assert lite_call.kwargs["model"] == "gemini-3.5-flash-lite"
    # -lite 模型嚴禁傳入 thinking_config 避免 400 INVALID_ARGUMENT
    assert "thinking_config" not in lite_call.kwargs["config"]
    assert lite_call.kwargs["config"]["response_mime_type"] == "application/json"

    # 驗證 response_schema 絕不含 additional_properties
    schema_lite = lite_call.kwargs["config"]["response_schema"]
    schema_lite_dump = getattr(schema_lite, "model_dump", None)
    schema_lite_repr = str(schema_lite_dump(exclude_none=True) if schema_lite_dump else schema_lite).lower()
    assert "additionalproperties" not in schema_lite_repr
    assert "additional_properties" not in schema_lite_repr

    # 驗證終端機 logger 有輸出警告與切換資訊
    assert "gemini-3.8-flash" in caplog.text
    assert "gemini-3.5-flash-lite" in caplog.text


def test_gemini_all_models_fail_logging(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)

    from google.genai.errors import ServerError
    # 所有呼叫皆拋出錯誤
    client.models.generate_content.side_effect = ServerError(503, "Server overloaded")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(TagExtractionError, match="Gemini API 呼叫失敗"):
            extract_meme_tags("所有模型皆掛掉", "gemini", model="gemini-3.8-flash")

    # 驗證終端機日誌有輸出 error 與 exc_info
    assert "Gemini 所有主力與備援模型皆呼叫失敗" in caplog.text
    assert "Server overloaded" in caplog.text


def test_extract_tags_from_image(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)

    monkeypatch.setattr("services.analyzer.load_known_tags", lambda: ["jealous_girl", "looking_back", "mygo"])
    client.models.generate_content.return_value = NS(parsed={
        "tags": ["mygo", "jealous_girl", "looking_back"],
        "description": "用回頭偷瞄別人的畫面吐槽注意力突然被吸走。",
    })

    result = analyze_image_with_gemini(b"fake_image_bytes", "image/jpeg")
    assert result.tags == ["jealous_girl", "looking_back"]
    assert result.description == "用回頭偷瞄別人的畫面吐槽注意力突然被吸走。"
    client.models.generate_content.assert_called_once()
    config = client.models.generate_content.call_args.kwargs["config"]
    schema = config["response_schema"]
    assert set(schema.required) == {"tags", "description"}
    assert schema.properties["tags"].min_items == 2
    assert schema.properties["tags"].max_items == 3
    schema_dump = getattr(schema, "model_dump", None)
    schema_repr = str(schema_dump(exclude_none=True) if schema_dump else schema).lower()
    assert "additionalproperties" not in schema_repr
    assert "additional_properties" not in schema_repr

    template = _load_image_tagger_template()
    assert "{known_tags_str}" in template
    assert _load_image_tagger_template() is template
    prompt = client.models.generate_content.call_args.kwargs["contents"][1]
    assert "jealous_girl, looking_back" in prompt
    assert "mygo" not in prompt
    assert "嚴禁自創標籤" in prompt
    assert "優先解析圖片文字（中文、英文、日文）與 <user_description> 中的台詞" in prompt
    assert "1 個情境／行為標籤" in prompt
    assert '{"tags":' in prompt


@pytest.mark.parametrize("known_tags", [["confused"], []])
def test_image_analysis_preserves_valid_partial_results(monkeypatch, caplog, known_tags):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr("services.analyzer.load_known_tags", lambda: known_tags)
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
    client.models.generate_content.return_value = NS(parsed={
        "tags": ["confused", "not-a-tag", "new_two"],
        "description": "用這個表情回應讓人一時摸不著頭緒的發言。",
    })

    assert analyze_image_with_gemini(b"fake_image_bytes", "image/jpeg").tags == known_tags
    assert f"保留 {len(known_tags)} 個已知標籤" in caplog.text


def test_extract_tags_from_image_with_description(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)

    client.models.generate_content.return_value = NS(
        parsed=None, text='{"tags":["burning_out","panic","midnight_coding"],"description":"半夜改不完程式時用來表達焦慮與崩潰。"}'
    )

    monkeypatch.setattr("services.analyzer.load_known_tags", lambda: ["burning_out", "panic"])
    # 1. 帶有有效 description
    result = analyze_image_with_gemini(
        b"fake_image_bytes", "image/jpeg", description="半夜三點寫程式改不出來的我"
    )
    assert result.tags == ["burning_out", "panic"]
    prompt_with_desc = client.models.generate_content.call_args.kwargs["contents"][1]
    assert "<user_description>" in prompt_with_desc
    assert "半夜三點寫程式改不出來的我" in prompt_with_desc

    # 2. 空白或 None description 不應注入 <user_description>
    client.models.generate_content.reset_mock()
    analyze_image_with_gemini(b"fake_image_bytes", "image/jpeg", description="   ")
    prompt_blank = client.models.generate_content.call_args.kwargs["contents"][1]
    assert "</user_description>" not in prompt_blank

    client.models.generate_content.reset_mock()
    analyze_image_with_gemini(b"fake_image_bytes", "image/jpeg", description=None)
    prompt_none = client.models.generate_content.call_args.kwargs["contents"][1]
    assert "</user_description>" not in prompt_none


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_chat_search_generates_external_keywords_without_changing_catalog(monkeypatch, tmp_path, provider):
    catalog = tmp_path / "known_tags.json"
    catalog.write_text('["speechless"]', encoding="utf-8")
    monkeypatch.setattr("services.analyzer.load_known_tags", partial(load_known_tags, catalog))
    client = MagicMock()
    raw_text = '{"matched_known_tags":["unknown"],"fallback_search_tags":["Distracted Boyfriend"]}'
    if provider == "gemini":
        monkeypatch.setenv("GEMINI_API_KEY", "test")
        monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
        call = client.models.generate_content
        call.return_value = NS(parsed=None, text=raw_text)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
        call = client.chat.completions.create
        call.return_value = NS(choices=[NS(message=NS(content=raw_text))])

    result = analyze_chat_search("走路時回頭看別人，女友很生氣", provider)

    assert result.matched_known_tags == []
    assert result.fallback_search_tags == ["distracted_boyfriend"]
    assert "不要硬湊 speechless" in str(call.call_args.kwargs)
    assert catalog.read_text(encoding="utf-8") == '["speechless"]'


def test_chat_search_recovers_known_fallback_tags(monkeypatch):
    monkeypatch.setattr("services.analyzer.load_known_tags", lambda: ["despair", "giving_up", "pleading"])
    monkeypatch.setattr("services.analyzer.load_work_tags", lambda: set())
    generate = MagicMock(return_value=NS(parsed={
        "matched_known_tags": [],
        "fallback_search_tags": ["hopeless", "despair", "giving_up"],
    }))
    monkeypatch.setattr("services.analyzer._generate_gemini_text", generate)

    result = analyze_chat_search("還能重歸於好嗎？我覺得沒救了。", "gemini")

    assert result.matched_known_tags == ["despair", "giving_up"]
    assert result.fallback_search_tags == ["hopeless"]
    prompt = generate.call_args.args[0]
    assert "已知標籤只可放入 matched_known_tags" in prompt
    assert "還能重歸於好嗎」可依語氣選 pleading、relationship_drama 或 regret" in prompt


@pytest.mark.parametrize(("text", "reference", "expected"), [
    ("加班都加到快崩潰了", "", ["overtime_work"]),
    ("加班都加到快崩潰了", "為什麼要演奏春日影", ["overtime_work"]),
    ("想找MyGO!!!!!的梗圖", "", ["mygo", "overtime_work"]),
    ("為什麼要演奏春日影？", "為什麼要演奏春日影", ["mygo", "overtime_work"]),
])
def test_chat_search_requires_a_work_reference(monkeypatch, text, reference, expected):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr("services.analyzer.load_known_tags", lambda: ["mygo", "overtime_work"])
    client = MagicMock()
    monkeypatch.setattr(genai, "Client", lambda **kwargs: client)
    client.models.generate_content.return_value = NS(parsed={
        "matched_known_tags": ["mygo", "overtime_work"],
        "fallback_search_tags": [],
        "work_reference": reference,
    })

    assert analyze_chat_search(text, "gemini").matched_known_tags == expected
    prompt = client.models.generate_content.call_args.kwargs["contents"]
    assert "通用標籤：overtime_work" in prompt
    assert "作品／專有標籤：mygo" in prompt
