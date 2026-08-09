import requests
import urllib.parse
import json
import sys

BASE = "https://api.ngc.nvidia.com/v2/search/catalog/resources/ENDPOINT"


def query(page):
    q = {
        "query": "*:*",
        "page": page,
        "pageSize": 24,
        "scoredSize": 24,
        "groupBy": "resourceType",
        "filters": [
            {"field": "orgName", "value": "qc69jvmznzxy"},
            {"field": "resourceId", "value": "-(qc69jvmznzxy/fidelity) OR -(qc69jvmznzxy/fluent) OR -(qc69jvmznzxy/spectre-x) OR -(qc69jvmznzxy/star-ccm)"},
            {"field": "label", "value": "-(\"blueprint\")"},
            {"field": "resourceType", "value": "endpoint"},
            {"field": "notAccessType", "value": "NOT_LISTED"},
            {"field": "isPublic", "value": "true"},
        ],
        "orderBy": [{"field": "dateCreated", "value": "DESC"}],
        "fields": ["labels", "name", "resource_id"],
    }
    url = f"{BASE}?q={urllib.parse.quote(json.dumps(q))}&group-labels-by-labelset=true"
    headers = {"Accept-Encoding": "gzip, deflate"}
    data = requests.get(url, headers=headers, timeout=30).json()
    for grp in data.get("results", []):
        if grp.get("groupValue") == "ENDPOINT":
            return grp["resources"]
    return data["results"][0]["resources"]


def fetch_free_endpoints():
    seen, free = set(), []
    for p in range(6):
        for r in query(p):
            rid = r["resourceId"]
            if rid in seen:
                continue
            seen.add(rid)
            labels = {l["key"]: l for l in r.get("labels", [])}
            nim = labels.get("nimType", {}).get("unresolvedValues", [])
            if "nim_type_preview" in nim:
                pub = labels["publisher"]["unresolvedValues"][0]
                name = f"{pub}/{r['name']}"
                general = " ".join(labels.get("general", {}).get("values", [])).lower()
                free.append((name, general))
    return free


EXCLUDE = [
    "embedding", "retriever", "rerank", "ranking", "tts", "speech", "voice",
    "vision", "vlm", "visual", "image", "ocr", "video", "captioning",
    "question answering", "doc intelligence", "protein", "biology", "bionemo",
    "safety", "guard", "moderation", "translation", "autonomous", "vehicles",
    "bev", "robotics", "broadcast", "smpte", "forensics", "speaker",
    "denoising", "calibration", "synthetic", "multimodal",
]
INCLUDE = [
    "text-to-text", "text-generation", "language generation", "chat",
    "reasoning", "instruction following", "agentic",
]


def is_text_summary_model(general):
    g = general.lower()
    if not any(k in g for k in INCLUDE):
        return False
    if any(k in g for k in EXCLUDE):
        return False
    return True


def main():
    free = fetch_free_endpoints()
    text_summary = [name for name, general in free if is_text_summary_model(general)]
    text_summary.sort()

    if "--text-summary" in sys.argv:
        print(f"可用于『文字输入 -> 总结 -> 文字输出』的模型: {len(text_summary)}")
        print("\n".join(text_summary))
    else:
        print(f"Free Endpoint 数量: {len(free)}")
        print("\n".join(sorted(name for name, _ in free)))


if __name__ == "__main__":
    main()
