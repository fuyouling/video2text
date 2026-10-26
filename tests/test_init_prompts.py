import pytest
from faster_whisper import WhisperModel


pytestmark = pytest.mark.skip(reason="requires GPU and model download")


def test_init_prompts():
    from src.utils.paths import ensure_cuda_libs

    ensure_cuda_libs()
    from faster_whisper import WhisperModel

    model_size = "models/faster-whisper-large-v3-turbo-ct2"
    model = WhisperModel(model_size, device="cuda", compute_type="float16")

    scene_text = "国内电商教学教程，含淘宝、拼多多、抖音、快手、小红书等平台运营、直播带货、短视频带货内容，请精准识别电商专业术语，简体中文输出。"
    hotwords_candidates = [
        "抖店", "淘宝", "拼多多", "小红书", "快手电商", "淘系",
        "SKU", "一件代发", "无货源",
        "UV价值", "GMV",
        "千川", "随心推", "DOU+", "憋单", "拉停留", "达人带货", "精选联盟", "挂车视频",
        "起店", "破零", "测款", "打爆单品", "拉新", "促活", "复购", "私域流量", "公域流量", "精细化运营", "铺货", "动销",
    ]

    def trim_hotwords(model, candidates, max_total=220):
        acc = []
        for kw in candidates:
            trial = " ".join(acc + [kw])
            if len(model.hf_tokenizer.encode(" " + trial.strip()).ids) > max_total:
                break
            acc.append(kw)
        return " ".join(acc)

    hotwords = trim_hotwords(model, hotwords_candidates)
    segments, info = model.transcribe(
        "video/out_16k_mono.wav",
        beam_size=5,
        language="zh",
        temperature=0.0,
        hotwords=hotwords,
        condition_on_previous_text=False,
    )
    for segment in segments:
        print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))
