"""
Validates backend/data/ground_truth.json against the assignment's test-set
requirements (see Assignment §5 and MASTER_IMPLEMENTATION_PLAN Phase 2, task 4):
  - exactly 20 ground-truth calls
  - every rule R1-R6 appears (present=true) at least twice
  - at least 6 calls are clean (all rules false)
  - every call_id has a corresponding .wav file in backend/data/audio/

Read-only: does not touch the database. Exits non-zero on any failure.
"""

import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).parent.parent
GROUND_TRUTH_PATH = BACKEND_DIR / "data" / "ground_truth.json"
AUDIO_DIR = BACKEND_DIR / "data" / "audio"

RULE_CODES = ["R1", "R2", "R3", "R4", "R5", "R6"]
EXPECTED_CALL_COUNT = 20
MIN_PER_RULE = 2
MIN_CLEAN_CALLS = 6


def load_ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.exists():
        print(f"FAIL: {GROUND_TRUTH_PATH} does not exist")
        sys.exit(1)
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    data = load_ground_truth()
    calls = data.get("calls", {})
    errors = []

    # 1. Exactly 20 calls
    if len(calls) != EXPECTED_CALL_COUNT:
        errors.append(f"Expected {EXPECTED_CALL_COUNT} calls, found {len(calls)}")

    # 2. Per-rule coverage (>=2 present=true) and clean-call count
    rule_counts = {code: 0 for code in RULE_CODES}
    clean_count = 0
    missing_audio = []

    for call_id, call_data in sorted(calls.items()):
        violations = call_data.get("violations", {})

        missing_codes = [c for c in RULE_CODES if c not in violations]
        if missing_codes:
            errors.append(f"{call_id}: missing rule keys {missing_codes}")

        is_clean = True
        for code in RULE_CODES:
            if violations.get(code) is True:
                rule_counts[code] += 1
                is_clean = False
        if is_clean:
            clean_count += 1

        # 3. Audio file presence
        # call_id format "CALL-0001" -> expected file "call_01.wav"
        try:
            call_num = int(call_id.split("-")[1])
        except (IndexError, ValueError):
            errors.append(f"{call_id}: cannot parse call number from id")
            continue
        audio_file = AUDIO_DIR / f"call_{call_num:02d}.wav"
        if not audio_file.exists():
            missing_audio.append(audio_file.name)

    for code in RULE_CODES:
        if rule_counts[code] < MIN_PER_RULE:
            errors.append(
                f"Rule {code}: only {rule_counts[code]} positive example(s), "
                f"need >= {MIN_PER_RULE}"
            )

    if clean_count < MIN_CLEAN_CALLS:
        errors.append(f"Only {clean_count} clean calls, need >= {MIN_CLEAN_CALLS}")

    # Report
    print("=" * 70)
    print("Ground truth label coverage check")
    print("=" * 70)
    print(f"Total calls:        {len(calls)} (expected {EXPECTED_CALL_COUNT})")
    print(f"Clean calls:        {clean_count} (need >= {MIN_CLEAN_CALLS})")
    print("Per-rule positive counts (need >= 2 each):")
    for code in RULE_CODES:
        print(f"  {code}: {rule_counts[code]}")
    print()

    if missing_audio:
        print(f"AUDIO NOT YET RECORDED: {len(missing_audio)}/{EXPECTED_CALL_COUNT} "
              f"expected .wav files are missing from {AUDIO_DIR}:")
        for name in missing_audio:
            print(f"  - {name}")
        print("This is expected until the role-play recordings are made; it does")
        print("not block label-coverage validation below, but blocks Phase 5+.")
        print()

    if errors:
        print("FAILURES:")
        for e in errors:
            print(f"  - {e}")
        print()
        print(f"RESULT: FAIL ({len(errors)} issue(s))")
        sys.exit(1)

    print("RESULT: PASS (label coverage requirements satisfied)")
    if missing_audio:
        print("NOTE: audio files are still outstanding (see above) — label")
        print("coverage passes, but calls cannot be processed until recorded.")
    sys.exit(0)


if __name__ == "__main__":
    main()
