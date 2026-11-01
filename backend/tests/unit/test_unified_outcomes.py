"""
Absolute expected outcomes of the (single) Unified analysis. Each case states what MUST be stored for a fixed model
answer: no comparison with another implementation, so a wrong rule cannot hide behind agreement.

  * evidence      app/services/finding_evidence.py     (reference or quote-only, echo checks, rejection kinds)
  * R6 opening    app/services/opening_service.py      (evaluate_opening, unreliable_opening)
  * persistence   app/services/pipeline.py             (speaker holds, R1 from start_time, scoring)

Everything here is offline: the model's answers are fixed JSON, no provider is reached.
"""

from datetime import datetime

import pytest

from app.models import Call, TranscriptSegment, Violation
from app.services import llm_service as llm, pipeline, transcription_service, unified_analysis_service as ua
from app.services.llm_service import LabelledSegment
from tests.integration.test_pipeline import make_queued_call, scratch_audio, write_fake_audio  # noqa: F401  (scratch_audio is a fixture)
from tests.unit.test_unified_analysis import comp, opening_of, response, seg_entry

# ---------------------------------------------------------------------------------------------- evidence
TEXTS = ["aap bewakoof ho bilkul", "hum aapke ghar bhi aayenge aur police bhi", "theek hai"]


def confident(speakers=("agent", "agent", "borrower"), texts=TEXTS, confidence=0.95):
    return [LabelledSegment(seq=i, speaker=s, start=i * 3.0, end=i * 3.0 + 3.0, text=t, confidence=confidence) for i, (s, t) in enumerate(zip(speakers, texts))]


def findings(entries, labelled=None):
    accepted, rejected = ua._validate_findings({"violations": entries}, labelled or confident(), {})
    return [(v.rule_code, v.segment_seq, v.quote, v.needs_review) for v in accepted], [(r["kind"], r["reason"]) for r in rejected]


def entry(idx=0, rule="R2", **extra):
    return {"rule_code": rule, "segment_index": idx, "explanation": "x", "confidence": 0.9, "speaker": "agent", **extra}


CASES = {
    "reference, echo matches the span": entry(0, word_start=1, word_end=1, quote="bewakoof"),
    "reference, no echo": entry(0, word_start=1, word_end=1),
    "reference, two-word span": entry(1, rule="R3", word_start=5, word_end=7),
    "reference, echo elsewhere in the SAME segment (held)": entry(1, rule="R3", word_start=0, word_end=1, quote="police bhi"),
    "reference, echo found only in ANOTHER segment (conflict)": entry(0, word_start=0, word_end=1, quote="theek hai"),
    "reference, out of range": entry(0, word_start=0, word_end=99),
    "reference, start after end": entry(0, word_start=3, word_end=1),
    "reference, negative": entry(0, word_start=-1, word_end=1),
    "reference, boolean position": entry(0, word_start=True, word_end=2),
    "reference, float position": entry(0, word_start=1.0, word_end=2),
    "reference, only word_start (never falls back to the quote)": entry(0, word_start=1, quote="bewakoof"),
    "quote-only, verbatim": entry(0, quote="bewakoof ho"),
    "quote-only, case and spacing differ": entry(0, quote="  BEWAKOOF   ho "),
    "quote-only, not in the segment": entry(0, quote="completely made up"),
    "quote-only, copied position markers but verbatim after stripping": entry(0, quote="0:aap 1:bewakoof"),
    "quote-only, markers around words that are not there": entry(0, quote="0:aap 1:zzz"),
    "no quote and no reference": entry(0),
    "cited segment does not exist": entry(9, quote="bewakoof"),
}


# What each case MUST come to (so that both pipelines agreeing cannot hide that both are wrong).
# accepted: (rule, segment, stored quote, held for review) ; None: rejected as invalid evidence.
EXPECTED = {
    "reference, echo matches the span": ("R2", 0, "bewakoof", False),
    "reference, no echo": ("R2", 0, "bewakoof", False),
    "reference, two-word span": ("R3", 1, "aur police bhi", False),
    "reference, echo elsewhere in the SAME segment (held)": ("R3", 1, "hum aapke", True),
    "reference, echo found only in ANOTHER segment (conflict)": None,
    "reference, out of range": None,
    "reference, start after end": None,
    "reference, negative": None,
    "reference, boolean position": None,
    "reference, float position": None,
    "reference, only word_start (never falls back to the quote)": None,
    "quote-only, verbatim": ("R2", 0, "bewakoof ho", False),
    "quote-only, case and spacing differ": ("R2", 0, "BEWAKOOF   ho", False),
    "quote-only, not in the segment": None,
    "quote-only, copied position markers but verbatim after stripping": ("R2", 0, "aap bewakoof", False),
    "quote-only, markers around words that are not there": None,
    "no quote and no reference": None,
    "cited segment does not exist": None,
}


@pytest.mark.parametrize("name", list(CASES))
def test_each_evidence_case_comes_to_its_expected_outcome(name):
    accepted, rejected = findings([CASES[name]])
    expected = EXPECTED[name]
    assert accepted == ([expected] if expected else [])
    assert len(rejected) == (0 if expected else 1)
    assert all(kind == "invalid_evidence" for kind, _ in rejected)


def test_a_quote_only_rejection_says_the_quote_is_not_verbatim():
    _, rejected = findings([entry(0, quote="made up")])
    assert rejected == [("invalid_evidence", "quote is not verbatim text of the cited segment")]


def test_urdu_text_with_a_quote_in_another_script_is_accepted_by_reference():
    urdu = ["ہم آپ کے گھر بھی آئیں گے"]
    e = entry(0, rule="R3", word_start=0, word_end=6, quote="हम आप के घर भी आएँगे")  # the model re-typed it in Devanagari
    accepted, rejected = findings([e], confident(("agent",), urdu))
    assert accepted == [("R3", 0, urdu[0], False)] and rejected == []  # the stored quote is the stored Urdu text


def test_evidence_diagnostics_count_reference_echo_and_quote_only_findings():
    unified: dict = {}
    ua._validate_findings({"violations": [entry(0, word_start=1, word_end=1, quote="bewakoof"), entry(0, quote="bewakoof ho")]}, confident(), {}, unified)
    assert unified == {"reference_findings": 1, "echo_matches_span": 1, "quote_only_findings": 1}


# ---------------------------------------------------------------------------------------------- R6 opening
OPEN_TEXT = "Namaste main Pooja bol rahi hoon Shaha Finlease se loan payment ke baare mein"


def unified_opening(labelled, name=None, company=None, purpose=None):
    data = response([l.speaker for l in labelled], opening=opening_of([[0]], name or comp(True, 0, "main Pooja bol rahi hoon"), company or comp(True, 0, "Shaha Finlease"), purpose or comp(True, 0, "loan payment ke baare mein")))
    diagnostics: dict = {}
    violation, held = ua._evaluate_opening(data, labelled, diagnostics)
    return violation, ([held] if held else []), diagnostics


GOOD = {"name_stated": True, "company_stated": True, "purpose_stated": True, "evidence_quote": "main Pooja bol rahi hoon"}


def open_segments(first_speaker="agent", confidence=0.95, text=OPEN_TEXT):
    return [LabelledSegment(0, first_speaker, 0.0, 4.0, text, confidence), LabelledSegment(1, "borrower" if first_speaker == "agent" else "agent", 4.0, 6.0, "haan boliye", 0.95)]


def verdict(violation, held):
    return ("violation" if violation else "held" if held else "ok")


def test_a_complete_opening_passes():
    violation, held, _ = unified_opening(open_segments())
    assert verdict(violation, held) == "ok"


def test_a_missing_company_is_a_violation_listing_it():
    no_company = "Namaste main Pooja bol rahi hoon loan payment ke baare mein"  # really has no company (the matcher would find one)
    violation, held, _ = unified_opening(open_segments(text=no_company), company=comp(False))
    assert verdict(violation, held) == "violation" and "company name" in violation.explanation


def test_a_borrower_labelled_first_segment_holds_the_opening():
    violation, held, _ = unified_opening(open_segments(first_speaker="borrower"), name=comp(False))
    assert verdict(violation, held) == "held" and "first segment" in held[0]["entry"]["explanation"]


def test_an_uncertain_speaker_label_in_the_opening_holds_it():
    no_company = "Namaste main Pooja bol rahi hoon loan payment ke baare mein"
    violation, held, diagnostics = unified_opening(open_segments(confidence=0.4, text=no_company), company=comp(False))
    assert verdict(violation, held) == "held"  # would otherwise be a deducting violation
    assert "unreliable_opening" in diagnostics["r6"] and "uncertain" in held[0]["entry"]["explanation"]


def test_missing_speaker_confidence_counts_as_uncertain():
    violation, held, _ = unified_opening(open_segments(confidence=None))
    assert verdict(violation, held) == "held"  # a complete opening is not passed on an unverified label either


def test_the_held_reason_is_stated():
    _, held, _ = unified_opening(open_segments(confidence=0.4))
    assert held[0]["reason"] == "the opening could not be evaluated reliably"


def test_a_cited_name_that_is_not_credible_is_held():
    text = "नमस्ते सि यो हूँ शाह फिनलीस से आपके लोन के पेमेंट के बारे में"
    labelled = [LabelledSegment(0, "agent", 0.0, 4.0, text, 0.95), LabelledSegment(1, "borrower", 4.0, 6.0, "हाँ", 0.95)]
    data = response(["agent", "borrower"], opening=opening_of([[0]], comp(True, 0, "सि यो"), comp(True, 0, "शाह फिनलीस"), comp(True, 0, "लोन के पेमेंट")))
    diagnostics: dict = {}
    violation, held = ua._evaluate_opening(data, labelled, diagnostics)
    assert violation is None and held is not None and "name_not_credited" in diagnostics["r6"]


# ---------------------------------------------------------------------------------------------- the whole pipeline
def run(db, scratch_audio, monkeypatch, call_id, start_time, speakers, confidences, finding):
    call = make_queued_call(db, call_id=call_id)
    call.start_time = start_time
    db.commit()
    write_fake_audio(scratch_audio, "pipe.wav")
    texts = [OPEN_TEXT, "agar paisa nahi aaya to police bhej dunga", "theek hai"]
    monkeypatch.setattr(transcription_service, "transcribe", lambda b, f, sleep_fn=None: [transcription_service.RawSegment(seq=i, start=i * 3.0, end=i * 3.0 + 3.0, text=t) for i, t in enumerate(texts)])
    sentiment = {"borrower_overall": "neutral", "agent_stayed_calm": True}
    answer = response([seg_entry(i, s, c) for i, (s, c) in enumerate(zip(speakers, confidences))],
                      violations=[{**finding, "speaker": "agent"}] if finding else None,
                      opening=opening_of([[0, 1]], comp(True, 0, "main Pooja bol rahi hoon"), comp(True, 0, "Shaha Finlease"), comp(True, 0, "loan payment ke baare mein")), sentiment=sentiment)
    monkeypatch.setattr(llm, "_get_client", lambda: object())
    monkeypatch.setattr(llm, "_chat_json", lambda client, messages, sleep_fn=None: answer)
    pipeline.process_call(call_id, db)
    db.expire_all()
    rows = db.query(Violation).filter_by(call_id=call_id).all()
    stored = sorted((v.rule_code, v.quote, v.severity, bool(v.needs_speaker_review), v.evidence_status, float(v.start_time_seconds or 0)) for v in rows)
    segments = [(s.seq, s.speaker, float(s.speaker_confidence) if s.speaker_confidence is not None else None) for s in db.query(TranscriptSegment).filter_by(call_id=call_id).order_by(TranscriptSegment.seq)]
    return stored, segments, db.get(Call, call_id).score, db.get(Call, call_id).status


REF = {"rule_code": "R3", "segment_index": 1, "word_start": 5, "word_end": 7, "explanation": "threat", "confidence": 0.9}
QUOTE_ONLY = {"rule_code": "R3", "segment_index": 1, "quote": "police bhej dunga", "explanation": "threat", "confidence": 0.9}
IN_HOURS = datetime(2026, 10, 7, 11, 0)
AGENT_AGENT_BORROWER = ["agent", "agent", "borrower"]


@pytest.mark.parametrize("finding", [REF, QUOTE_ONLY], ids=["reference evidence", "quote-only evidence"])
def test_a_verified_threat_is_stored_with_its_quote_and_deducts_30(db_session, scratch_audio, monkeypatch, finding):
    stored, _, score, status = run(db_session, scratch_audio, monkeypatch, "CALL-OUT-1", IN_HOURS, AGENT_AGENT_BORROWER, [0.95] * 3, finding)
    assert status == "ready" and score == 70
    assert [r[:5] for r in stored] == [("R3", "police bhej dunga", "critical", False, "verified")]
    assert stored[0][5] == 3.0  # starts at the cited segment's time


def test_a_finding_on_a_low_confidence_borrower_segment_is_kept_held_and_not_scored(db_session, scratch_audio, monkeypatch):
    stored, segments, score, _ = run(db_session, scratch_audio, monkeypatch, "CALL-OUT-2", IN_HOURS, ["agent", "borrower", "borrower"], [0.95, 0.45, 0.95], REF)
    assert len(stored) == 1 and stored[0][3] is True and score == 100
    assert segments[1] == (1, "borrower", 0.45)


@pytest.mark.parametrize("call_id,hhmm,expect_r1", [("CALL-0008", "07:30", True), ("CALL-0012", "14:00", False), ("CALL-0016", "15:00", False), ("CALL-0018", "20:30", True)])
def test_r1_comes_from_the_scenario_start_time(db_session, scratch_audio, monkeypatch, call_id, hhmm, expect_r1):
    h, m = map(int, hhmm.split(":"))
    stored, _, score, _ = run(db_session, scratch_audio, monkeypatch, f"{call_id}-X", datetime(2026, 10, 9, h, m), AGENT_AGENT_BORROWER, [0.95] * 3, None)
    r1 = [r for r in stored if r[0] == "R1"]
    assert bool(r1) is expect_r1
    if expect_r1:
        assert r1[0][1] == f"Call started at {hhmm}:00 IST (outside permitted hours 08:00-19:00)" and score == 85
    else:
        assert score == 100


@pytest.mark.parametrize("hhmm,expect_r1", [("07:59", True), ("08:00", False), ("18:59", False), ("19:00", True)])
def test_the_permitted_window_is_08_00_inclusive_to_19_00_exclusive(db_session, scratch_audio, monkeypatch, hhmm, expect_r1):
    h, m = map(int, hhmm.split(":"))
    stored, _, _, _ = run(db_session, scratch_audio, monkeypatch, f"CALL-WIN-{h}{m}", datetime(2026, 10, 9, h, m), AGENT_AGENT_BORROWER, [0.95] * 3, None)
    assert any(r[0] == "R1" for r in stored) is expect_r1


def test_r1_does_not_depend_on_what_the_model_says(db_session, scratch_audio, monkeypatch):
    r1_claim = {"rule_code": "R1", "segment_index": 0, "quote": "Namaste", "explanation": "late", "confidence": 0.9}
    stored, _, score, _ = run(db_session, scratch_audio, monkeypatch, "CALL-OUT-R1M", datetime(2026, 10, 9, 14, 0), AGENT_AGENT_BORROWER, [0.95] * 3, r1_claim)
    assert [r for r in stored if r[0] == "R1"] == [] and score == 100
