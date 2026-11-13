from urllib.parse import parse_qs, urlparse

from src.services import wecom_service


def _configure(monkeypatch, enabled: bool = True) -> None:
    monkeypatch.setattr(wecom_service.settings, "wecom_enabled", enabled)
    monkeypatch.setattr(wecom_service.settings, "wecom_corp_id", "wwcampus")
    monkeypatch.setattr(wecom_service.settings, "wecom_agent_id", "1000002")
    monkeypatch.setattr(wecom_service.settings, "wecom_secret", "secret")
    monkeypatch.setattr(
        wecom_service.settings,
        "wecom_redirect_uri",
        "https://rag.example.edu/wecom/callback",
    )


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(wecom_service.settings, "wecom_enabled", False)
    assert not wecom_service.is_enabled()


def test_enabled_requires_full_config(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(wecom_service.settings, "wecom_secret", "")
    assert not wecom_service.is_enabled()
    monkeypatch.setattr(wecom_service.settings, "wecom_secret", "secret")
    assert wecom_service.is_enabled()


def test_authorize_url_uses_web_oauth(monkeypatch):
    _configure(monkeypatch)
    url = wecom_service.build_authorize_url("state-token")
    assert url.startswith("https://open.weixin.qq.com/connect/oauth2/authorize?")
    assert url.endswith("#wechat_redirect")

    query = parse_qs(urlparse(url).query)
    assert query["appid"] == ["wwcampus"]
    assert query["agentid"] == ["1000002"]
    assert query["scope"] == ["snsapi_base"]
    assert query["state"] == ["state-token"]
    assert query["redirect_uri"] == ["https://rag.example.edu/wecom/callback"]
