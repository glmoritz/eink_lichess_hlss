from hlss.services.llss import _normalize_llss_base_url


def test_normalize_llss_base_url_adds_api_root() -> None:
    assert _normalize_llss_base_url("http://pccasa.tutu.lan:8008") == "http://pccasa.tutu.lan:8008/api"


def test_normalize_llss_base_url_keeps_existing_api_root() -> None:
    assert _normalize_llss_base_url("http://pccasa.tutu.lan:8008/api") == "http://pccasa.tutu.lan:8008/api"
