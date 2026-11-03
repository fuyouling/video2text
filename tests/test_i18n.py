import json
from pathlib import Path
from unittest.mock import MagicMock

from src.i18n import I18N, available_languages, get_lang, install_qt_translator, set_lang, t


def test_i18n_fallback_and_keys():
    set_lang("zh-CN")
    assert get_lang() == "zh-CN"

    # Test newly added keys
    assert t("transcriber.model_download_failed") != "transcriber.model_download_failed"
    assert t("transcriber.transcribe_cancelled") != "transcriber.transcribe_cancelled"

    # Test English
    set_lang("en")
    assert get_lang() == "en"
    assert "cancel" in t("transcriber.transcribe_cancelled").lower()
    assert "download" in t("transcriber.model_download_failed").lower()

    # Reset back to zh-CN
    set_lang("zh-CN")


def test_i18n_formatting_safety():
    set_lang("zh-CN")
    # Missing args should return template safely without crash
    res = t("preprocessing.log.extract_start")
    assert "开始" in res

    # Passed args should format properly
    res_formatted = t("preprocessing.log.extract_start", label="提取", name="test.mp4")
    assert "提取" in res_formatted
    assert "test.mp4" in res_formatted


def test_i18n_zh_tw_alignment():
    locales_dir = Path(__file__).resolve().parent.parent / "src" / "i18n" / "locales"
    with open(locales_dir / "zh-CN.json", "r", encoding="utf-8") as f:
        cn = json.load(f)
    with open(locales_dir / "zh-TW.json", "r", encoding="utf-8") as f:
        tw = json.load(f)

    def flatten(d, prefix=""):
        items = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                items.update(flatten(v, key))
            else:
                items[key] = v
        return items

    cn_flat = flatten(cn)
    tw_flat = flatten(tw)

    missing = set(cn_flat.keys()) - set(tw_flat.keys())
    obsolete = set(tw_flat.keys()) - set(cn_flat.keys())

    assert len(missing) == 0, f"Missing keys in zh-TW: {missing}"
    assert len(obsolete) == 0, f"Obsolete keys in zh-TW: {obsolete}"
    assert len(tw_flat) == len(cn_flat)


def test_i18n_multi_language_required_keys():
    locales_dir = Path(__file__).resolve().parent.parent / "src" / "i18n" / "locales"
    check_locales = ["de", "es", "fr", "ja", "ko", "ru"]

    for loc in check_locales:
        with open(locales_dir / f"{loc}.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        dl = data.get("model_downloader", {}).get("download", {})
        assert "{source}" in dl.get("direct_ok", ""), f"{loc} direct_ok missing {{source}}"
        assert dl.get("mirror_ok"), f"{loc} missing mirror_ok"
        assert dl.get("switch_mirror"), f"{loc} missing switch_mirror"

        ak = data.get("api_key", {})
        for col in ["col_vendor", "col_key", "col_value", "col_action"]:
            assert ak.get(col), f"{loc} missing api_key.{col}"

        assert data.get("tx_labels", {}).get("funasr_compute_type"), f"{loc} missing tx_labels.funasr_compute_type"


def test_i18n_qt_translator_cleanup():
    app = MagicMock()
    # Mock QLibraryInfo and QTranslator to simulate loading
    from PySide6.QtCore import QTranslator
    from src.i18n import _QT_TRANSLATORS

    old_tr = QTranslator()
    _QT_TRANSLATORS.append(old_tr)

    install_qt_translator(app, "zh-CN")

    app.removeTranslator.assert_called()
    assert old_tr not in _QT_TRANSLATORS


def test_i18n_thread_safety():
    import threading

    errors = []

    def worker():
        try:
            for _ in range(50):
                set_lang("zh-CN")
                _ = t("common.ok")
                set_lang("en")
                _ = t("common.ok")
                _ = available_languages()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    set_lang("zh-CN")
    assert errors == []
