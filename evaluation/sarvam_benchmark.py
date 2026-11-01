"""
EVALUATION-ONLY benchmark script. Not imported by, or wired into, the
application. Compares Sarvam AI's batch STT (with diarization) against
Groq Whisper on copies of the 5 real recordings. Reads/writes only inside
the scratch directories passed on the command line -- never touches
backend/data/audio or backend/data/compliance.db.
"""

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import os
from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError


def main(input_dir: str, output_dir: str) -> None:
    api_key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not api_key:
        print("SARVAM_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    files = sorted(str(p) for p in input_path.glob("call_0*.wav"))
    print(f"Files to process: {[Path(f).name for f in files]}")

    client = SarvamAI(api_subscription_key=api_key)

    job = client.speech_to_text_job.create_job(
        model="saaras:v4",
        mode="codemix",
        with_diarization=True,
        with_timestamps=True,
        language_code="unknown",
        num_speakers=2,
    )
    print(f"Job created: {job.job_id}")

    ok = job.upload_files(file_paths=files)
    print(f"Upload ok: {ok}")

    job.start()
    print("Job started, polling...")

    status = job.wait_until_complete(poll_interval=5, timeout=600)
    print(f"Final job status: {status}")

    results = job.get_file_results()
    (output_path / "raw_results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"Raw results saved to {output_path / 'raw_results.json'}")

    downloaded = job.download_outputs(output_dir=str(output_path / "downloaded"))
    print(f"download_outputs() returned: {downloaded}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
