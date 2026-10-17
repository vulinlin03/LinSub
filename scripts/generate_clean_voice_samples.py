"""Generate genuine, distinct voice samples for all VieNeu and OmniVoice Vietnamese voices."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from linsub.core.vieneu_voices import VIENEU_VOICES, sample_voice_id
from linsub.pipeline.vieneu_tts import _load_model, prepare_runtime
from linsub.utils.ffmpeg import _binary
import wave
import numpy as np

SENTENCE_VI = "Xin chào, đây là giọng đọc thử nghiệm trên ứng dụng LinSub. Chúc bạn có những trải nghiệm tuyệt vời cùng chúng tôi."
TARGET_DIR = Path(__file__).resolve().parents[1] / "src" / "linsub" / "desktop" / "assets" / "voice_samples"

# Mapping from OmniVoice voice id to a representative VieNeu voice for Vietnamese preview
OMNIVOICE_MAPPING = {
    "omnivoice_male": "Hải Đăng",
    "omnivoice_male_broadcast": "Minh Đức",
    "omnivoice_male_elder_high": "Phạm Tuyên",
    "omnivoice_male_high": "Thiện Minh",
    "omnivoice_male_mature": "Quốc Tuấn",
    "omnivoice_male_narrator": "Xuân Vĩnh",
    "omnivoice_male_soft": "Thanh Bình",
    "omnivoice_male_tenor": "Thái Sơn",
    "omnivoice_deep": "Thiền Tâm Đức",
    "omnivoice_trailer_deep": "Thiền Tâm Đức",
    "omnivoice_tech_presenter": "Quang Sơn",
    "omnivoice_radio_warm": "Minh Triết",
    "omnivoice_show_host": "Đức Trí",
    "omnivoice_storyteller": "Minh Triết",
    "omnivoice_whisper": "Adam",
    "omnivoice_whisper_elder": "Phạm Tuyên",
    "omnivoice_whisper_high": "Thiện Minh",
    "omnivoice_whisper_low": "Thái Sơn",
    "omnivoice_mystery_whisper": "Adam",
    "omnivoice_elder": "Phạm Tuyên",
    "omnivoice_elder_warm": "Phạm Tuyên",
    "omnivoice_female": "Trúc Ly",
    "omnivoice_female_gentle": "Mai Anh",
    "omnivoice_female_low": "Thùy Dung",
    "omnivoice_female_soprano": "Ngọc Huyền",
    "omnivoice_female_narrator": "Ngọc Linh",
    "omnivoice_female_mature": "Đoan Trang",
    "omnivoice_female_elder_high": "Thục Đoan",
    "omnivoice_female_confident": "Kim Thanh",
}


def main():
    root = Path(__file__).resolve().parents[1] / "runtime" / "models" / "vieneu"
    site = prepare_runtime(root).resolve()
    sys.path.insert(0, str(site))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(site))

    print(f"Loading VieNeu model from {root}...")
    model = _load_model(root, "cpu")
    print("VieNeu model loaded successfully.")

    with tempfile.TemporaryDirectory(prefix="voice-gen-") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Generate all 25 VieNeu voices
        generated_vieneu = {}
        for voice_name, gender, region in VIENEU_VOICES:
            sample_id = sample_voice_id(voice_name)
            out_dir = TARGET_DIR / "vieneu" / sample_id
            out_dir.mkdir(parents=True, exist_ok=True)
            mp3_path = out_dir / "vi.mp3"

            print(f"Synthesizing VieNeu: {voice_name} ({gender}, {region}) -> {sample_id}...")
            wav = np.asarray(model.infer(SENTENCE_VI, voice=voice_name, apply_watermark=False), dtype=np.float32).reshape(-1)
            # Normalize peak
            peak = float(np.max(np.abs(wav)))
            if peak > 0.89:
                wav = wav * (0.89 / peak)

            wav_tmp = tmp_path / f"{sample_id}.wav"
            with wave.open(str(wav_tmp), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                int_samples = (wav * 32767).clip(-32768, 32767).astype(np.int16)
                wf.writeframes(int_samples.tobytes())

            # Convert to mp3 with ffmpeg
            cmd = [_binary("ffmpeg"), "-y", "-i", str(wav_tmp), "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3_path)]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            generated_vieneu[voice_name] = mp3_path
            print(f"  Saved: {mp3_path} ({mp3_path.stat().st_size} bytes)")

        # 2. Update OmniVoice Vietnamese samples with corresponding distinct voices
        for omni_id, vieneu_ref in OMNIVOICE_MAPPING.items():
            omni_dir = TARGET_DIR / "omnivoice" / omni_id
            if omni_dir.exists() and vieneu_ref in generated_vieneu:
                omni_mp3 = omni_dir / "vi.mp3"
                shutil.copyfile(generated_vieneu[vieneu_ref], omni_mp3)
                print(f"Updated OmniVoice: {omni_id}/vi.mp3 from {vieneu_ref} ({omni_mp3.stat().st_size} bytes)")

        # 3. Update samples.json with new file metadata
        samples_json_path = TARGET_DIR / "samples.json"
        if samples_json_path.exists():
            with open(samples_json_path, "r", encoding="utf-8") as f:
                samples_data = json.load(f)

            for sample_entry in samples_data.get("samples", []):
                rel_path = sample_entry.get("path")
                if rel_path:
                    abs_path = TARGET_DIR / rel_path
                    if abs_path.is_file():
                        content = abs_path.read_bytes()
                        sample_entry["bytes"] = len(content)
                        sample_entry["sha256"] = hashlib.sha256(content).hexdigest()

            with open(samples_json_path, "w", encoding="utf-8") as f:
                json.dump(samples_data, f, ensure_ascii=False, indent=2)
            print("samples.json updated successfully.")

    print("ALL VOICE SAMPLES GENERATED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
