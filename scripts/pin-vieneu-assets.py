"""Refresh reviewed VieNeu manifests from official immutable upstream artifacts.

Development helper only; production downloads go through model_bootstrap.
Prints the manifest for review, never edits source or installs dependencies.
"""
import hashlib
import json
import urllib.request


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main():
    assets = []
    repos = {
        "pnnbao-ump/VieNeu-TTS-v3-Turbo": (
            "v3-turbo", lambda name: name.startswith(("update/", "onnx_update/"))
            or name in {"speaker_encoder.onnx", "denoiser.onnx", "config.json"}),
        "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX": (
            "codec", lambda name: name in {
                "moss_audio_tokenizer_decode_full.onnx", "moss_audio_tokenizer_decode_shared.data",
                "moss_audio_tokenizer_decode_step.onnx", "codec_browser_onnx_meta.json",
                "moss_audio_tokenizer_encode.onnx", "moss_audio_tokenizer_encode.data"}),
        "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano": (
            "codec-gpu", lambda name: name.endswith((".json", ".py", ".safetensors"))),
    }
    revisions = {}
    for repo, (directory, selected) in repos.items():
        info = json.loads(fetch(f"https://huggingface.co/api/models/{repo}?blobs=true"))
        revision = info["sha"]
        revisions[repo] = revision
        for item in info["siblings"]:
            name = item["rfilename"]
            if not selected(name):
                continue
            url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}?download=true"
            digest = (item.get("lfs") or {}).get("sha256") or hashlib.sha256(fetch(url)).hexdigest()
            assets.append(dict(path=f"{directory}/{name}", size=item["size"], sha256=digest, url=url))
    for package, version in (("vieneu", "3.8.3"), ("sea-g2p", "0.10.0"), ("kaldi-native-fbank", "1.22.3")):
        info = json.loads(fetch(f"https://pypi.org/pypi/{package}/{version}/json"))
        wheels = [item for item in info["urls"] if item["filename"].endswith(".whl")
                  and ("py3-none-any" in item["filename"] or "cp313-cp313-win_amd64" in item["filename"]
                       or "cp310-abi3-win_amd64" in item["filename"])]
        if len(wheels) != 1:
            raise RuntimeError(f"Expected one Windows/Python 3.13 wheel for {package}: {wheels}")
        item = wheels[0]
        assets.append(dict(path="sdk/" + item["filename"], size=item["size"],
                           sha256=item["digests"]["sha256"], url=item["url"]))
    print(json.dumps(dict(sdk_version="3.8.3", revisions=revisions, assets=assets), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
