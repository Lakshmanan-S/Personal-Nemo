"""Unit tests for the whole Nemotron pipeline module (app/pipeline.py):
pricing/cost cap, the LLM wrapper, extraction, privacy tagging, synthesis,
and the daily nudge. All Nemotron calls are mocked - no real API key needed.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


import pytest
from pydantic import BaseModel


from app import pipeline
from app.db import BlockedTrait, Fact, ModelCall, RawItem, Trait




class _Thing(BaseModel):
    value: str




def _fake_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )




# ---- cost control ----




def test_calendar_learning_is_one_super_call_and_reuses_saved_candidates(db_session,user,calendar_raw):
    import json
    rows = [calendar_raw("one"),calendar_raw("two",summary="Client project meeting")]
    content = json.dumps({"events":[
        {"event_id":rows[0].id,"kind":"fitness","activity":"badminton","quote":"Badminton"},
        {"event_id":rows[1].id,"kind":"work","activity":"work","quote":"Client project meeting"},
    ]})
    with patch.object(pipeline,"_email_model_response",return_value=content) as model:
        result = pipeline.classify_calendar_events(db_session,user.id,rows)
        assert result[rows[0].id]["activity"] == "badminton"
        assert pipeline.classify_calendar_events(db_session,user.id,rows) == result
    model.assert_called_once()
    assert model.call_args.kwargs["model_size"] == "super"
    assert model.call_args.args[4] == "calendar_learning"
    assert "html_link" not in model.call_args.args[2] and "calendar@example.com" not in model.call_args.args[2]




@pytest.mark.parametrize("change",[
    {"event_id":"invented-id"}, {"quote":"not in title"}, {"kind":"fitness","activity":"tennis"}, {"kind":"work","activity":"work"},
])
def test_calendar_learning_rejects_bad_ids_or_evidence_without_paying_again(db_session,user,calendar_raw,change):
    import json
    raw = calendar_raw()
    content = json.dumps({"events":[{"event_id":raw.id,"kind":"fitness","activity":"badminton","quote":"Badminton",**change}]})
    with patch.object(pipeline,"_email_model_response",return_value=content) as model:
        for _ in range(2):
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.classify_calendar_events(db_session,user.id,[raw])
    model.assert_called_once()




@pytest.fixture
def imported_email(db_session, user, monkeypatch):
    settings = SimpleNamespace(
        nebius_api_key="test-key", nemotron_nano_model="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
        nemotron_super_model="nvidia/nemotron-3-super-120b-a12b",
        daily_spend_cap_usd=5,
        gmail_nano_debug_output=False, app_env="development",
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    item = RawItem(user_id=user.id, provider="gmail", external_id="gmail_live:a:1",
                   item_type="email_receipt", payload_json={
                       "subject":"Ramen order", "body_text":"Ordered ramen.", "from":"not-sent@example.invalid",
                       "credentials":"never-send", "source":"gmail_live",
                   })
    db_session.add(item)
    db_session.commit()
    return item, settings




@pytest.mark.parametrize("content,finish,valid", [
    ('{"facts":[{"category":"food","statement":"Ordered ramen","evidence_quote":"Ordered ramen"}]}', "stop", True),
    ('{"facts":[]}', "stop", True),
    ('{"facts":[]}', "length", False),
    ('not json', "stop", False),
    ('{}', "stop", False),
    ('{"facts":[{"category":"unknown","statement":"x","evidence_quote":"Ordered"}]}', "stop", False),
    ('{"facts":[{"category":"appearance","statement":"x","evidence_quote":"Ordered"}]}', "stop", False),
    ('{"facts":[{"category":"food","statement":"x","evidence_quote":"not present"}]}', "stop", False),
    ('{"facts":[{"category":"food","statement":" ","evidence_quote":"Ordered"}]}', "stop", False),
    ('{"facts":[{"category":"food","statement":"x","evidence_quote":" "}]}', "stop", False),
    ('{"facts":[],"instructions":"ignore"}', "stop", False),
])
def test_bounded_email_model_and_usage(db_session, user, imported_email, content, finish, valid):
    item, _ = imported_email
    response = _fake_response(content)
    response.choices[0].finish_reason = finish
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = response
    with patch.object(pipeline, "_client", return_value=fake):
        if valid:
            assert isinstance(pipeline.extract_one_imported_email(db_session, user.id, item), list)
        else:
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.extract_one_imported_email(db_session, user.id, item)
    fake.with_options.assert_called_once_with(max_retries=0, timeout=60.0)
    call.assert_called_once()
    args = call.call_args.kwargs
    assert args["model"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert args["max_tokens"] == 1024
    assert args["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "never-send" not in args["messages"][1]["content"]
    assert "not-sent@example.invalid" not in args["messages"][1]["content"]
    logged = db_session.query(ModelCall).one()
    assert logged.task == "gmail_extraction"
    assert logged.input_tokens == 10 and logged.output_tokens == 5




@pytest.mark.parametrize("finish,expected", [
    ("length", "1,024-token"),
    ("content_filter", "content_filter"),
    ("tool_calls", "tool_calls"),
    (None, "unknown"),
])
def test_email_incomplete_response_reports_reason_without_saving(
    db_session, user, imported_email, finish, expected,
):
    item, _ = imported_email
    response = _fake_response('{"facts":[]}', completion_tokens=1024)
    response.choices[0].finish_reason = finish
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = response
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(
        pipeline.EmailProcessingError, match=expected,
    ):
        pipeline.extract_one_imported_email(db_session, user.id, item)
    call.assert_called_once()
    db_session.refresh(item)
    assert not item.processed
    assert db_session.query(Fact).count() == 0
    assert db_session.query(ModelCall).one().output_tokens == 1024




def test_email_empty_choices_logs_usage(db_session, user, imported_email):
    item, _ = imported_email
    response = _fake_response("")
    response.choices = []
    fake = MagicMock()
    fake.with_options.return_value.__enter__.return_value.chat.completions.create.return_value = response
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(
        pipeline.EmailProcessingError, match="no completion",
    ):
        pipeline.extract_one_imported_email(db_session, user.id, item)
    assert db_session.query(ModelCall).count() == 1
    assert not item.processed




@pytest.mark.parametrize("enabled,environment,should_log", [
    (False, "development", False),
    (True, "production", False),
    (True, "development", True),
])
@pytest.mark.parametrize("finish,content", [
    ("stop", "invalid private model output\n\x1b[31m"),
    ("length", '{"facts":['),
])
def test_email_debug_output_is_opt_in_and_development_only(
    db_session, user, imported_email, caplog, enabled, environment, should_log, finish, content,
):
    import json
    item, settings = imported_email
    settings.gmail_nano_debug_output = enabled
    settings.app_env = environment
    response = _fake_response(content)
    response.choices[0].finish_reason = finish
    response.choices[0].message.reasoning_content = "private reasoning must not be logged"
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = response
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(pipeline.EmailProcessingError):
        pipeline.extract_one_imported_email(db_session, user.id, item)
    records = [r for r in caplog.records if "GMAIL_NANO_DEBUG" in r.message]
    assert len(records) == int(should_log)
    if should_log:
        diagnostic = json.loads(records[0].message.split("GMAIL_NANO_DEBUG ", 1)[1])
        assert diagnostic["content"] == content
        assert diagnostic["finish_reason"] == finish
        assert diagnostic["output_tokens"] == 5
        assert diagnostic["reasoning_characters"] == len(response.choices[0].message.reasoning_content)
        assert "\x1b" not in records[0].message and "\n" not in records[0].message
    assert "private reasoning must not be logged" not in caplog.text
    assert "Ordered ramen." not in caplog.text
    assert "test-key" not in caplog.text
    call.assert_called_once()




def test_email_debug_output_bounds_content_and_handles_missing_usage(db_session, user, imported_email, caplog):
    import json
    item, settings = imported_email
    settings.gmail_nano_debug_output = True
    response = _fake_response("x" * 20000)
    response.choices[0].finish_reason = "length"
    response.usage = None
    fake = MagicMock()
    fake.with_options.return_value.__enter__.return_value.chat.completions.create.return_value = response
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(pipeline.EmailProcessingError):
        pipeline.extract_one_imported_email(db_session, user.id, item)
    record = next(r for r in caplog.records if "GMAIL_NANO_DEBUG" in r.message)
    diagnostic = json.loads(record.message.split("GMAIL_NANO_DEBUG ", 1)[1])
    assert len(diagnostic["content"]) == 16000 and diagnostic["content_truncated"]
    assert diagnostic["output_tokens"] is None




@pytest.mark.parametrize("labels,valid", [
    (lambda ids: [{"email_id": id, "type": "food_order"} for id in ids], True),
    (lambda ids: [{"email_id": ids[0], "type": "food_order"}], False),
    (lambda ids: [{"email_id": ids[0], "type": "food_order"}] * 2, False),
    (lambda ids: [{"email_id": id, "type": "invented"} for id in ids], False),
    (lambda ids: [{"email_id": "invented", "type": "food_order"} for id in ids], False),
])
def test_batch_classifier_checks_exact_ids(db_session, user, imported_email, labels, valid):
    import json
    item, _ = imported_email
    other = RawItem(user_id=user.id, provider="gmail", external_id="gmail_live:a:2",
                    item_type="email_receipt", payload_json={})
    db_session.add(other)
    db_session.commit()
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"emails": labels([item.id, other.id])})) as call:
        if valid:
            assert len(pipeline.classify_imported_emails(db_session, user.id, [item, other])) == 2
        else:
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.classify_imported_emails(db_session, user.id, [item, other])
    assert call.call_args.args[-1] == "gmail_classification"
    with pytest.raises(pipeline.EmailProcessingError):
        pipeline.classify_imported_emails(db_session, user.id, [])




@pytest.mark.parametrize("kind,event,valid", [
    ("food_order", {"dishes": [{"value": "Dosa", "quote": "Dosa"}], "restaurant": {"value":"Cafe A","quote":"Cafe A"}, "order_id": None}, True),
    ("sports_booking", {"sport":{"value":"Badminton","quote":"Badminton"},"venue":{"value":"Arena","quote":"Arena"}, "order_id":None}, True),
    ("travel_booking", {"destination":{"value":"Paris","quote":"Paris"},"order_id":None}, True),
    ("food_order", {"dishes":[{"value":"Dosa","quote":"invented"}],"restaurant":None,"order_id":None}, False),
    ("food_order", {"dishes":[{"value":"Discount","quote":"Discount"}],"restaurant":None,"order_id":None}, False),
    ("food_order", {"dishes":[{"value":"Dosa","quote":"Dosa"}],"restaurant":None,"order_id":None,"amount":177}, False),
    ("food_order", {"dishes":[],"restaurant":None,"order_id":None}, False),
    ("food_order", {"dishes":[{"value":"not grounded","quote":"Dosa"}],"restaurant":None,"order_id":None}, False),
])
def test_category_extraction_schema_and_evidence(db_session, user, imported_email, kind, event, valid):
    import json
    item, _ = imported_email
    item.payload_json = {"body_text":"Dosa Cafe A Badminton Arena Paris Discount"}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)) as call:
        if valid:
            result = pipeline.extract_email_event(db_session, user.id, item, kind)
            assert result == event
        else:
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.extract_email_event(db_session, user.id, item, kind)
        assert call.call_args.args[-1] == "gmail_" + kind
    with patch.object(pipeline, "_email_model_response") as call, pytest.raises(pipeline.EmailProcessingError):
        pipeline.extract_email_event(db_session, user.id, item, "promotion")
    call.assert_not_called()




@pytest.mark.parametrize("quote,valid", [
    ("Order ID: ABC123", True),
    ("Booking #ABC123", True),
    ("Price ABC123", False),
])
def test_order_identifier_requires_labeled_evidence(db_session, user, imported_email, quote, valid):
    import json
    item, _ = imported_email
    item.payload_json = {"body_text":"Dosa. " + quote}
    event = {"dishes":[{"value":"Dosa","quote":"Dosa"}],"restaurant":None,
             "order_id":{"value":"ABC123","quote":quote}}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)):
        if valid:
            assert pipeline.extract_email_event(db_session, user.id, item, "food_order") == event
        else:
            with pytest.raises(pipeline.EmailProcessingError, match="labeled"):
                pipeline.extract_email_event(db_session, user.id, item, "food_order")




def test_sports_email_formatting_and_identifier_quote_reconstruction(db_session, user, imported_email):
    import json
    item, _ = imported_email
    item.payload_json = {"body_text":
        "your booking *Box Cricket at Arena,\n Bengaluru* has been confirmed.\nBooking ID : *TEST123*"}
    event = {"sport":{"value":"Box Cricket","quote":"*Box Cricket*"},
             "venue":{"value":"Arena, Bengaluru","quote":"your booking *Box Cricket at Arena, Bengaluru* has been confirmed."},
             "order_id":{"value":"TEST123","quote":"Booking ID:*TEST123*"}}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)):
        result = pipeline.extract_email_event(db_session, user.id, item, "sports_booking")
    assert result["sport"]["value"] == "Box Cricket"
    assert result["venue"]["value"] == "Arena, Bengaluru"
    for field in result.values():
        assert field["quote"] in item.payload_json["body_text"]
    assert result["order_id"]["quote"] == "Booking ID : *TEST123"




@pytest.mark.parametrize("source,value,quote,valid", [
    ("Arena &amp; Cafe", "Arena & Cafe", "Arena & Cafe", True),
    ("Arena\u00a0  Bengaluru", "Arena Bengaluru", "Arena Bengaluru", True),
    ("Arena-Bengaluru", "Arena Bengaluru", "Arena Bengaluru", False),
    ("Arena B", "Arena A", "Arena A", False),
    ("Badminton booked. Arena", "Badminton", "invented quote", False),
])
def test_evidence_normalizes_formatting_not_meaning(db_session, user, imported_email, source, value, quote, valid):
    import json
    item, _ = imported_email
    item.payload_json = {"body_text":source}
    event = {"sport":{"value":value,"quote":quote},"venue":None,"order_id":None}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)):
        if valid:
            result = pipeline.extract_email_event(db_session, user.id, item, "sports_booking")
            assert result["sport"]["quote"] in source
        else:
            with pytest.raises(pipeline.EmailProcessingError, match="sport"):
                pipeline.extract_email_event(db_session, user.id, item, "sports_booking")




def test_schema_valid_failed_evidence_is_cached_without_new_paid_call(db_session, user, imported_email):
    import json
    from app.db import EmailIngestion
    item, _ = imported_email
    item.payload_json = {"body_text":"Badminton at Arena"}
    db_session.add(EmailIngestion(raw_item_id=item.id, user_id=user.id, kind="sports_booking", status="classified"))
    db_session.commit()
    event = {"sport":{"value":"Tennis","quote":"Tennis"},"venue":None,"order_id":None}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)) as call:
        for _ in range(2):
            with pytest.raises(pipeline.EmailProcessingError, match="sport"):
                pipeline.extract_email_event(db_session, user.id, item, "sports_booking")
        call.assert_called_once()
    assert db_session.get(EmailIngestion, item.id).event["candidate"] == event
    assert db_session.query(Fact).count() == 0 and not item.processed




@pytest.mark.parametrize("concept,valid", [
    ({"dish":"Chicken Peri Peri Burger","group":"burgers_sandwiches","family":"chicken burgers",
      "quote":"Chicken Peri Peri Burger"}, True),
    ({"dish":"Chicken Peri Peri Burger","group":"burgers_sandwiches","family":"chicken burgers",
      "quote":"Invented description"}, False),
    ({"dish":"Invented item","group":"chinese","family":"noodles","quote":"Invented item"}, False),
    ({"dish":"Chicken Peri Peri Burger","group":"other","family":"a mystery",
      "quote":"Chicken Peri Peri Burger"}, False),
    ({"dish":"Chicken Peri Peri Burger","group":"burgers_sandwiches","family":"chicken burgers",
      "quote":" Chicken Peri Peri Burger "}, False),
])
def test_food_concepts_are_source_bound_not_new_observations(db_session, user, imported_email, concept, valid):
    import json
    item, _ = imported_email
    item.payload_json = {"body_text":"Chicken Peri Peri Burger"}
    event = {"dishes":[{"value":"Chicken Peri Peri Burger","quote":"Chicken Peri Peri Burger"}],
             "restaurant":None, "order_id":None, "concepts":[concept]}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)) as call:
        if valid:
            assert pipeline.extract_email_event(db_session, user.id, item, "food_order") == event
        else:
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.extract_email_event(db_session, user.id, item, "food_order")
        call.assert_called_once()




def test_food_concepts_reject_duplicate_dish_assignments(db_session, user, imported_email):
    import json
    item, _ = imported_email
    item.payload_json = {"body_text":"Dosa"}
    concept = {"dish":"Dosa","group":"south_indian","family":"dosa","quote":"Dosa"}
    event = {"dishes":[{"value":"Dosa","quote":"Dosa"}],"restaurant":None,"order_id":None,
             "concepts":[concept, concept]}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)):
        with pytest.raises(pipeline.EmailProcessingError, match="Duplicate dish concepts"):
            pipeline.extract_email_event(db_session, user.id, item, "food_order")




def test_today_extraction_requires_grounded_dates_and_caches_candidate(db_session, user, today_email, today_candidate):
    import json
    item = today_email()
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[today_candidate()]})) as call:
        assert len(pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")) == 1
        assert len(pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")) == 1
        call.assert_called_once()
        assert call.call_args.kwargs["model_size"] == "super"
    with patch.object(pipeline, "_email_model_response", side_effect=AssertionError("No paid retry")):
        item.payload_json = {**item.payload_json, "body_text":"Changed source"}
        db_session.commit()
        with pytest.raises(pipeline.EmailProcessingError, match="source changed"):
            pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")




@pytest.mark.parametrize("updates", [
    {"quote":"Invented request"},
    {"day":"2026-10-31"},
    {"kind":"reminder","at":None},
    {"at":"2026-10-30T17:00:00"},
    {"title":"Review"},
    {"when_quote":"No deadline was given"},
])
def test_today_rejects_unsupported_or_ambiguous_outputs(db_session, user, today_email, today_candidate, updates):
    import json
    item = today_email()
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[today_candidate(**updates)]})):
        with pytest.raises(pipeline.EmailProcessingError):
            pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")




@pytest.mark.parametrize("body,changes,zone,valid", [
    ("Badminton on 2026-10-30 at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", True),
    ("Badminton on 2026-10-30 at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T16:00:00+05:30"}, "Asia/Kolkata", False),
    ("Badminton on 2026-10-30.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", False),
    ("Badminton on 2026-10-30 at 11 PM PDT.", {"day":"2026-10-31","at":"2026-10-30T23:00:00-07:00"}, "Asia/Kolkata", True),
    ("Badminton on 2026-10-30 at 3 PM UTC.", {"day":"2026-10-30","at":"2026-10-30T15:00:00Z"}, "Asia/Kolkata", True),
    ("Badminton on 2026-10-30 at 3 PM IST.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", True),
    ("Badminton on 2026-10-30 at 3 PM IST.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Europe/London", False),
    ("Meeting on 2026-11-01 at 1:30 AM.", {"day":"2026-11-01","at":"2026-11-01T01:30:00-04:00"}, "America/New_York", False),
    ("Meeting on 2026-11-01 at 1:30 AM EDT.", {"day":"2026-11-01","at":"2026-11-01T01:30:00-04:00"}, "America/New_York", True),
    ("Meeting at 2026-10-30T15:00:00Z.", {"day":"2026-10-30","at":"2026-10-30T15:00:00Z"}, "Asia/Kolkata", True),
    ("Meeting at 2026-10-30T15:00:00+05:30.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", True),
    ("Meeting at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", False),
    ("Meeting on 10/11/2026 at 3 PM.", {"day":"2026-11-10","at":"2026-11-10T15:00:00+05:30"}, "Asia/Kolkata", False),
    ("Meeting on 30/10/2026 at noon.", {"day":"2026-10-30","at":"2026-10-30T12:00:00+05:30"}, "Asia/Kolkata", True),
    ("Meeting on Friday at 3 PM.", {"day":"2026-10-31","at":"2026-10-31T15:00:00+05:30"}, "Asia/Kolkata", False),
    ("Meeting on October 30, 2026 at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", True),
    ("Meeting on 30th October 2026 at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", True),
    ("Meeting on November 30, 2026 at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", False),
    ("Meeting in October 2026 at 3 PM.", {"day":"2026-10-30","at":"2026-10-30T15:00:00+05:30"}, "Asia/Kolkata", False),
    ("Game 9:30 AM-11:30 AM on 24-08-2025.", {"day":"2025-08-24","at":"2025-08-24T09:30:00+05:30"}, "Asia/Calcutta", True),
    ("Game 09:30-11:30 on 24-08-2025.", {"day":"2025-08-24","at":"2025-08-24T09:30:00+05:30"}, "Asia/Calcutta", True),
    ("Game 09:30 -11:30 on 24-08-2025.", {"day":"2025-08-24","at":"2025-08-24T09:30:00+05:30"}, "Asia/Calcutta", True),
    ("Meeting at 2026-10-30T15:00-07:00.", {"day":"2026-10-31","at":"2026-10-30T15:00:00-07:00"}, "Asia/Calcutta", True),
    ("Meeting on 2026-10-30 at 3 PM UTC-07:00.", {"day":"2026-10-31","at":"2026-10-30T15:00:00-07:00"}, "Asia/Calcutta", True),
    ("Game 9:30 AM-11:30 AM on 24-08-2025.", {"day":"2025-08-24","at":"2025-08-24T10:30:00+05:30"}, "Asia/Calcutta", False),
])
def test_today_clocks_and_timezone_evidence(db_session, user, today_email, today_candidate, body, changes, zone, valid):
    import json
    item = today_email(body)
    candidate = today_candidate(kind="reminder", title="Scheduled game", quote=body, when_quote=body, **changes)
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[candidate]})):
        if valid:
            assert len(pipeline.extract_today_candidates(db_session, user.id, item, zone)) == 1
        else:
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.extract_today_candidates(db_session, user.id, item, zone)




def test_today_relative_dates_use_receipt_not_current_date(db_session, user, today_email, today_candidate):
    import json
    item = today_email("Please review the proposal tomorrow.")
    candidate = today_candidate(day="2026-10-31", quote="Please review the proposal", when_quote="tomorrow")
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[candidate]})):
        assert pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")[0]["day"] == "2026-10-31"
    item.occurred_at = None
    db_session.commit()
    with patch.object(pipeline, "_email_model_response", side_effect=AssertionError("Must reuse or reject cached data")):
        with pytest.raises(pipeline.EmailProcessingError, match="source changed"):
            pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")




@pytest.mark.parametrize("when_quote", ["Please complete it by today.", "EOD", "end of day", "end of the day"])
def test_today_eod_deadline_is_date_only_and_cached(db_session, user, today_email, today_deadline, when_quote):
    import json
    body, candidate = today_deadline
    if when_quote.startswith("end"):
        body = body.replace("EOD", when_quote)
        candidate["quote"] = candidate["quote"].replace("EOD", when_quote)
    candidate["when_quote"] = when_quote
    item = today_email(body)
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[candidate]})) as call:
        for _ in range(2):
            result = pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Calcutta")
            assert result[0]["day"] == "2026-10-30" and result[0]["at"] is None
        call.assert_called_once()
        assert "EOD" in call.call_args.args[3] and "date-only" in call.call_args.args[3]




@pytest.mark.parametrize("suffix,day,received", [
    ("", "2026-10-31", True),
    ("", "2026-10-30", False),
    (" tomorrow", "2026-10-31", True),
    (" on 2026-11-02", "2026-11-02", True),
])
def test_today_eod_requires_receipt_or_explicit_date(db_session, user, today_email, today_candidate, suffix, day, received):
    import json
    item = today_email("Please submit the diagram by EOD" + suffix)
    if not received:
        item.occurred_at = None
        db_session.commit()
    candidate = today_candidate(day=day, quote="Please submit the diagram", when_quote="EOD" + suffix)
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[candidate]})):
        if suffix:
            assert pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Calcutta")[0]["day"] == day
        else:
            with pytest.raises(pipeline.EmailProcessingError, match="receipt date"):
                pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Calcutta")




def test_today_reference_uses_real_source_label_not_model_formatting(db_session, user, today_email, today_candidate):
    import json
    item = today_email("Please review the proposal today, 2026-10-30, by 5 PM.\nTask ID : *TASK-1*")
    candidate = today_candidate(reference={"value":"TASK-1","quote":"Task ID:TASK-1"})
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[candidate]})) as call:
        for _ in range(2):
            result = pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Calcutta")
            assert result[0]["reference"] == {"value":"TASK-1","quote":"Task ID : *TASK-1"}
        call.assert_called_once()




def test_today_invalid_json_is_cached_without_a_second_paid_call(db_session, user, today_email):
    item = today_email()
    with patch.object(pipeline, "_email_model_response", return_value="not json") as call:
        for _ in range(2):
            with pytest.raises(pipeline.EmailProcessingError, match="Invalid Today"):
                pipeline.extract_today_candidates(db_session, user.id, item, "UTC")
        call.assert_called_once()




@pytest.mark.parametrize("reference,valid", [
    ({"value":"TASK-1","quote":"Task ID: *TASK-1*"}, True),
    ({"value":"task-1","quote":"Task ID: *TASK-1*"}, False),
    ({"value":"50","quote":"Price: 50"}, False),
    ({"value":"TASK-2","quote":"Task ID: TASK-2"}, False),
])
def test_today_reference_is_labeled_and_source_bound(db_session, user, today_email, today_candidate, reference, valid):
    import json
    item = today_email("Please review the proposal today, 2026-10-30, by 5 PM.\nTask ID: *TASK-1*\nPrice: 50")
    candidate = today_candidate(reference=reference)
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps({"items":[candidate]})):
        if valid:
            assert pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")[0]["reference"]["value"] == "TASK-1"
        else:
            with pytest.raises(pipeline.EmailProcessingError):
                pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")




def test_today_provider_request_is_bounded_and_logs_usage(db_session, user, imported_email, today_email, today_candidate):
    import json
    _, settings = imported_email
    item = today_email()
    response = _fake_response(json.dumps({"items":[today_candidate()]}))
    response.choices[0].finish_reason = "stop"
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = response
    with patch.object(pipeline, "_client", return_value=fake):
        assert len(pipeline.extract_today_candidates(db_session, user.id, item, "Asia/Kolkata")) == 1
    assert call.call_args.kwargs["model"] == settings.nemotron_super_model
    assert call.call_args.kwargs["max_tokens"] == 2048
    assert call.call_args.kwargs["response_format"]["json_schema"]["name"] == "TodayExtraction"
    assert db_session.query(ModelCall).one().task == "gmail_today"
    fake.with_options.assert_called_once_with(max_retries=0, timeout=60.0)




def test_cached_extraction_blocks_changed_source_without_paid_retry(db_session, user, imported_email):
    import json
    from app.db import EmailIngestion
    item, _ = imported_email
    item.payload_json = {"body_text":"Badminton"}
    db_session.add(EmailIngestion(raw_item_id=item.id, user_id=user.id, kind="sports_booking", status="classified"))
    db_session.commit()
    event = {"sport":{"value":"Tennis","quote":"Tennis"},"venue":None,"order_id":None}
    with patch.object(pipeline, "_email_model_response", return_value=json.dumps(event)) as call:
        with pytest.raises(pipeline.EmailProcessingError):
            pipeline.extract_email_event(db_session, user.id, item, "sports_booking")
        item.payload_json = {"body_text":"Tennis"}
        db_session.commit()
        with pytest.raises(pipeline.EmailProcessingError, match="source changed"):
            pipeline.extract_email_event(db_session, user.id, item, "sports_booking")
        call.assert_called_once()




@pytest.mark.parametrize("source,value,valid", [
    ("Booking ID : *TEST123*", "TEST123", True),
    ("Booking ID : *TEST123*", "test123", False),
    ("Booking ID : *TEST1234*", "TEST123", False),
    ("Price: TEST123", "TEST123", False),
])
def test_identifier_reconstruction_preserves_identity(source, value, valid):
    evidence = pipeline.EvidenceValue(value=value, quote="model label can differ")
    quote = pipeline._source_quote(evidence, [source], identifier=True)
    assert (quote is not None) == valid
    if quote:
        assert quote in source




@pytest.mark.parametrize("kind,event", [
    ("food_order", {"dishes":[{"value":"Dosa","quote":"Dosa"}],"restaurant":None,
                    "order_id":{"value":"TEST123","quote":"Order ID: TEST123"}}),
    ("sports_booking", {"sport":{"value":"Badminton","quote":"Badminton"},"venue":None,
                        "order_id":{"value":"TEST123","quote":"Booking ID: TEST123"}}),
    ("travel_booking", {"destination":{"value":"Paris","quote":"Paris"},
                        "order_id":{"value":"TEST123","quote":"Booking ID: TEST123"}}),
])
def test_super_extraction_requests_schema_and_logs_correct_model(db_session, user, imported_email, caplog, kind, event):
    import json
    item, settings = imported_email
    settings.gmail_nano_debug_output = True
    item.payload_json = {"body_text":"Dosa Badminton Paris " + event["order_id"]["quote"]}
    response = _fake_response(json.dumps(event))
    response.choices[0].finish_reason = "stop"
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = response
    with patch.object(pipeline, "_client", return_value=fake):
        assert pipeline.extract_email_event(db_session, user.id, item, kind) == event
    call.assert_called_once()
    args = call.call_args.kwargs
    assert args["model"] == settings.nemotron_super_model
    assert args["response_format"]["type"] == "json_schema"
    contract = args["response_format"]["json_schema"]
    assert contract["strict"] is True
    schema = contract["schema"]
    assert "order_id" in schema["required"]
    assert schema["additionalProperties"] is False
    assert "anyOf" in schema["properties"]["order_id"]
    assert '{"value":"EXAMPLE123","quote":"Booking ID: EXAMPLE123"}' in args["messages"][0]["content"]
    assert "JSON Schema" in args["messages"][0]["content"]
    fake.with_options.assert_called_once_with(max_retries=0, timeout=60.0)
    logged = db_session.query(ModelCall).one()
    assert logged.model == "super" and logged.model_id == settings.nemotron_super_model
    assert logged.cost_usd == pytest.approx(pipeline.estimate_cost_usd("super", 10, 5))
    diagnostic = json.loads(next(r.message.split("GMAIL_NANO_DEBUG ",1)[1] for r in caplog.records if "GMAIL_NANO_DEBUG" in r.message))
    assert diagnostic["model"] == "super" and diagnostic["model_id"] == settings.nemotron_super_model




@pytest.mark.parametrize("kind,event", [
    ("food_order", {"dishes":[{"value":"Dosa","quote":"Dosa"}],"restaurant":None,"order_id":"TEST123"}),
    ("sports_booking", {"sport":{"value":"Badminton","quote":"Badminton"},"venue":None,"order_id":"TEST123"}),
])
def test_super_rejects_bare_identifier_logs_usage_and_keeps_email_pending(db_session, user, imported_email, kind, event):
    import json
    item, settings = imported_email
    item.payload_json = {"body_text":"Dosa Badminton Booking ID: TEST123"}
    response = _fake_response(json.dumps(event))
    response.choices[0].finish_reason = "stop"
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.return_value = response
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(
        pipeline.EmailProcessingError, match=r"Super \(fields: order_id\)",
    ) as error:
        pipeline.extract_email_event(db_session, user.id, item, kind)
    assert "TEST123" not in str(error.value)
    call.assert_called_once()
    db_session.refresh(item)
    assert not item.processed and db_session.query(Fact).count() == 0
    assert db_session.query(ModelCall).one().model == "super"




@pytest.mark.parametrize("cap,configured,error", [
    (0.0007, "nvidia/nemotron-3-super-120b-a12b", pipeline.BudgetExceededError),
    (5, "unexpected-model", pipeline.EmailProcessingError),
])
def test_super_preflight_uses_super_rates_and_rejects_unverified_model(db_session, user, imported_email, cap, configured, error):
    item, settings = imported_email
    settings.daily_spend_cap_usd = cap
    settings.nemotron_super_model = configured
    # 0.0007 can cover this small Nano request but not capped Super output alone ($0.0009216).
    with patch.object(pipeline, "_client") as client, pytest.raises(error):
        pipeline.extract_email_event(db_session, user.id, item, "food_order")
    client.assert_not_called()
    assert db_session.query(ModelCall).count() == 0




def test_super_provider_rejecting_schema_does_not_fallback_or_retry(db_session, user, imported_email):
    import httpx
    from openai import BadRequestError
    item, _ = imported_email
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
    call.side_effect = BadRequestError("schema unsupported; private response", response=response, body=None)
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(pipeline.EmailProcessingError) as error:
        pipeline.extract_email_event(db_session, user.id, item, "food_order")
    call.assert_called_once()
    assert "private response" not in str(error.value)
    assert not item.processed
    assert db_session.query(ModelCall).count() == 0


@pytest.mark.parametrize("key,model,cap,error", [
    ("", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",5,pipeline.EmailProcessingError),
    ("test", "super",5,pipeline.EmailProcessingError),
    ("test", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",0.000001,pipeline.BudgetExceededError),
])
def test_email_preflight_no_model_call(db_session, user, imported_email, key, model, cap, error):
    item, settings = imported_email
    settings.nebius_api_key, settings.nemotron_nano_model, settings.daily_spend_cap_usd = key, model, cap
    with patch.object(pipeline, "_client") as client, pytest.raises(error):
        pipeline.extract_one_imported_email(db_session, user.id, item)
    client.assert_not_called()




def test_email_provider_failure_safe_and_missing_usage_explicit(db_session, user, imported_email):
    from openai import APIConnectionError
    import httpx
    item, _ = imported_email
    fake = MagicMock()
    call = fake.with_options.return_value.__enter__.return_value.chat.completions.create
    call.side_effect = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"), message="secret-body")
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(pipeline.EmailProcessingError) as error:
        pipeline.extract_one_imported_email(db_session, user.id, item)
    assert "secret-body" not in str(error.value)
    call.assert_called_once()
    call.side_effect = None
    call.return_value = SimpleNamespace(usage=None)
    with patch.object(pipeline, "_client", return_value=fake), pytest.raises(pipeline.EmailProcessingError, match="Spending is unknown"):
        pipeline.extract_one_imported_email(db_session, user.id, item)
    assert db_session.query(ModelCall).count() == 0




def test_get_today_spend_usd_sums_only_todays_calls(db_session, user):
    db_session.add_all(
        [
            ModelCall(user_id=user.id, task="chat", model="super", model_id="x", cost_usd=1.0),
            ModelCall(user_id=user.id, task="chat", model="super", model_id="x", cost_usd=2.5),
        ]
    )
    db_session.commit()


    assert pipeline.get_today_spend_usd(db_session) == pytest.approx(3.5)




def test_ensure_within_budget_passes_when_under_cap(db_session, user, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", lambda: type("S", (), {"daily_spend_cap_usd": 10.0})())
    db_session.add(ModelCall(user_id=user.id, task="chat", model="super", model_id="x", cost_usd=1.0))
    db_session.commit()


    pipeline.ensure_within_budget(db_session)  # should not raise




def test_ensure_within_budget_raises_when_cap_reached(db_session, user, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", lambda: type("S", (), {"daily_spend_cap_usd": 1.0})())
    db_session.add(ModelCall(user_id=user.id, task="chat", model="super", model_id="x", cost_usd=1.0))
    db_session.commit()


    with pytest.raises(pipeline.BudgetExceededError):
        pipeline.ensure_within_budget(db_session)




# ---- llm wrapper ----




def test_call_llm_returns_raw_string_without_response_model(db_session, user):
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _fake_response("hello there")))
    )
    with patch.object(pipeline, "_client", return_value=fake_client):
        result = pipeline.call_llm(
            db_session, task="chat", model_size="super", system_prompt="sys", user_prompt="hi", user_id=user.id
        )


    assert result == "hello there"
    logged = db_session.query(ModelCall).one()
    assert logged.task == "chat"
    assert logged.model == "super"
    assert logged.input_tokens == 10
    assert logged.output_tokens == 5
    assert logged.cost_usd > 0




def test_call_llm_validates_json_against_response_model(db_session, user):
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: _fake_response('{"value": "ok"}')))
    )
    with patch.object(pipeline, "_client", return_value=fake_client):
        result = pipeline.call_llm(
            db_session, task="extraction", model_size="nano", system_prompt="sys", user_prompt="hi",
            response_model=_Thing, user_id=user.id,
        )


    assert isinstance(result, _Thing)
    assert result.value == "ok"




def test_call_llm_retries_once_on_invalid_json_then_succeeds(db_session, user):
    responses = [_fake_response("not json"), _fake_response('{"value": "fixed"}')]
    calls = {"count": 0}


    def _create(**kwargs):
        response = responses[calls["count"]]
        calls["count"] += 1
        return response


    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    with patch.object(pipeline, "_client", return_value=fake_client):
        result = pipeline.call_llm(
            db_session, task="extraction", model_size="nano", system_prompt="sys", user_prompt="hi",
            response_model=_Thing, user_id=user.id,
        )


    assert result.value == "fixed"
    assert calls["count"] == 2  # one retry happened
    logged = db_session.query(ModelCall).one()
    assert logged.input_tokens == 20  # both calls' tokens accumulated (10 + 10)




def test_call_llm_raises_after_budget_cap_reached(db_session, user):
    db_session.add(ModelCall(user_id=user.id, task="chat", model="ultra", model_id="x", cost_usd=999.0))
    db_session.commit()


    with pytest.raises(pipeline.BudgetExceededError):
        pipeline.call_llm(db_session, task="chat", model_size="super", system_prompt="sys", user_prompt="hi", user_id=user.id)




def test_call_llm_stream_yields_chunks_and_logs_cost(db_session, user):
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3)),
    ]
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: iter(chunks))))
    with patch.object(pipeline, "_client", return_value=fake_client):
        collected = list(
            pipeline.call_llm_stream(
                db_session, task="chat", model_size="super", system_prompt="sys", user_prompt="hi", user_id=user.id
            )
        )


    assert "".join(collected) == "Hello"
    logged = db_session.query(ModelCall).one()
    assert logged.input_tokens == 7
    assert logged.output_tokens == 3




# ---- extraction ----




def _make_raw_item(db_session, user_id, external_id="gmail-1"):
    item = RawItem(
        user_id=user_id, provider="gmail", external_id=external_id, item_type="email_receipt",
        payload_json={"subject": "test"}, occurred_at=datetime.now(timezone.utc), processed=False,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item




def test_extraction_creates_facts_for_grounded_source_ids(db_session, user):
    item = _make_raw_item(db_session, user.id)
    fake_result = pipeline.ExtractionResult(
        facts=[pipeline.ExtractedFact(category="food", statement="Likes sushi", source_ids=[item.id])]
    )
    with patch.object(pipeline, "call_llm", return_value=fake_result):
        created = pipeline.run_extraction_for_user(db_session, user.id)


    assert created == 1
    fact = db_session.query(Fact).one()
    assert fact.category == "food"
    assert fact.source_item_ids == [item.id]
    db_session.refresh(item)
    assert item.processed is True




def test_extraction_drops_facts_with_ungrounded_source_ids(db_session, user):
    item = _make_raw_item(db_session, user.id)
    fake_result = pipeline.ExtractionResult(
        facts=[pipeline.ExtractedFact(category="food", statement="Hallucinated", source_ids=["not-a-real-id"])]
    )
    with patch.object(pipeline, "call_llm", return_value=fake_result):
        created = pipeline.run_extraction_for_user(db_session, user.id)


    assert created == 0
    assert db_session.query(Fact).count() == 0
    db_session.refresh(item)
    assert item.processed is True  # still marked processed so it isn't retried forever




def test_extraction_only_processes_unprocessed_items(db_session, user):
    _make_raw_item(db_session, user.id, external_id="gmail-1")
    already_done = _make_raw_item(db_session, user.id, external_id="gmail-2")
    already_done.processed = True
    db_session.commit()


    fake_result = pipeline.ExtractionResult(facts=[])
    with patch.object(pipeline, "call_llm", return_value=fake_result) as mocked:
        pipeline.run_extraction_for_user(db_session, user.id)


    assert mocked.call_count == 1  # only one batch call for the single unprocessed item




# ---- privacy tagging ----




def _make_fact(db_session, user_id, category="food", tier=None, statement="Likes ramen"):
    fact = Fact(user_id=user_id, category=category, statement=statement, source_item_ids=[], model_used="nano", privacy_tier=tier)
    db_session.add(fact)
    db_session.commit()
    db_session.refresh(fact)
    return fact




def test_privacy_tagger_applies_tags_from_model(db_session, user):
    fact = _make_fact(db_session, user.id)
    fake_result = pipeline.TaggingResult(tags=[pipeline.TaggedFact(fact_id=fact.id, tier="share", reason="Just food taste.")])


    with patch.object(pipeline, "call_llm", return_value=fake_result):
        tagged = pipeline.run_privacy_tagging_for_user(db_session, user.id)


    assert tagged == 1
    db_session.refresh(fact)
    assert fact.privacy_tier == "share"
    assert fact.tier_reason == "Just food taste."




def test_privacy_tagger_defaults_untagged_facts_to_ask(db_session, user):
    fact = _make_fact(db_session, user.id, category="routine", statement="Home address mentioned")
    fake_result = pipeline.TaggingResult(tags=[])  # model skipped this fact


    with patch.object(pipeline, "call_llm", return_value=fake_result):
        pipeline.run_privacy_tagging_for_user(db_session, user.id)


    db_session.refresh(fact)
    assert fact.privacy_tier == "ask"  # safety-net default, never silently "share"




def test_privacy_tagger_ignores_invalid_tier_values(db_session, user):
    fact = _make_fact(db_session, user.id)
    fake_result = pipeline.TaggingResult(tags=[pipeline.TaggedFact(fact_id=fact.id, tier="not-a-tier", reason="bad")])


    with patch.object(pipeline, "call_llm", return_value=fake_result):
        tagged = pipeline.run_privacy_tagging_for_user(db_session, user.id)


    assert tagged == 0
    db_session.refresh(fact)
    assert fact.privacy_tier == "ask"  # falls back to the safety net, not the bad value




# ---- synthesis ----




def test_synthesis_creates_new_trait_and_marks_facts_synthesized(db_session, user):
    fact = _make_fact(db_session, user.id, tier="share", statement="Orders sushi often")
    fake_result = pipeline.SynthesisResult(
        traits=[
            pipeline.SynthesizedTrait(
                key="favorite_cuisine", value="Japanese", summary="Based on 1 order", confidence=1.5,
                supporting_fact_ids=[fact.id],
            )
        ]
    )


    with patch.object(pipeline, "call_llm", return_value=fake_result):
        upserted = pipeline.run_synthesis_for_user(db_session, user.id)


    assert upserted == 1
    trait = db_session.query(Trait).one()
    assert trait.key == "favorite_cuisine"
    assert trait.confidence == 1.0  # clamped to [0, 1]
    assert trait.evidence_count == 1
    assert trait.privacy_tier == "share"
    db_session.refresh(fact)
    assert fact.synthesized is True




def test_synthesis_uses_most_restrictive_tier_among_supporting_facts(db_session, user):
    shareable = _make_fact(db_session, user.id, tier="share", statement="Orders sushi often")
    private_fact = _make_fact(db_session, user.id, tier="private", statement="Mentions home address")
    fake_result = pipeline.SynthesisResult(
        traits=[
            pipeline.SynthesizedTrait(
                key="routine_pattern", value="...", summary="...", confidence=0.8,
                supporting_fact_ids=[shareable.id, private_fact.id],
            )
        ]
    )


    with patch.object(pipeline, "call_llm", return_value=fake_result):
        pipeline.run_synthesis_for_user(db_session, user.id)


    trait = db_session.query(Trait).one()
    assert trait.privacy_tier == "private"




def test_synthesis_does_not_overwrite_user_overridden_trait(db_session, user):
    fact = _make_fact(db_session, user.id, tier="share", statement="Orders sushi often")
    existing = Trait(
        user_id=user.id, category="food", key="favorite_cuisine", value="Italian (user set)",
        confidence=0.9, user_overridden=True, privacy_tier="private",
    )
    db_session.add(existing)
    db_session.commit()


    fake_result = pipeline.SynthesisResult(
        traits=[
            pipeline.SynthesizedTrait(
                key="favorite_cuisine", value="Japanese", summary="...", confidence=0.9, supporting_fact_ids=[fact.id]
            )
        ]
    )
    with patch.object(pipeline, "call_llm", return_value=fake_result):
        pipeline.run_synthesis_for_user(db_session, user.id)


    db_session.refresh(existing)
    assert existing.value == "Italian (user set)"  # untouched
    assert existing.privacy_tier == "private"




def test_synthesis_skips_blocked_trait_keys(db_session, user):
    fact = _make_fact(db_session, user.id, tier="share", statement="Orders sushi often")
    db_session.add(BlockedTrait(user_id=user.id, category="food", key="favorite_cuisine"))
    db_session.commit()


    fake_result = pipeline.SynthesisResult(
        traits=[
            pipeline.SynthesizedTrait(
                key="favorite_cuisine", value="Japanese", summary="...", confidence=0.9, supporting_fact_ids=[fact.id]
            )
        ]
    )
    with patch.object(pipeline, "call_llm", return_value=fake_result):
        pipeline.run_synthesis_for_user(db_session, user.id)


    assert db_session.query(Trait).count() == 0




def test_synthesis_only_runs_for_categories_with_new_tagged_facts(db_session, user):
    _make_fact(db_session, user.id, category="food", tier="share")
    fake_result = pipeline.SynthesisResult(traits=[])


    with patch.object(pipeline, "call_llm", return_value=fake_result) as mocked:
        pipeline.run_synthesis_for_user(db_session, user.id)


    called_categories = [call.kwargs["user_prompt"].split("\n", 1)[0] for call in mocked.call_args_list]
    assert called_categories == ["Category: food"]




# ---- nudge ----




def test_nudge_combines_free_slot_and_top_taste_trait(db_session, user):
    db_session.add(
        RawItem(
            user_id=user.id, provider="gcal", external_id="e1", item_type="event",
            payload_json={"title": "Free evening - no plans", "start": "2025-10-12T18:00:00Z"},
            occurred_at=datetime.now(timezone.utc), processed=True,
        )
    )
    db_session.add(Trait(user_id=user.id, category="food", key="k1", value="Loves ramen", confidence=0.9, privacy_tier="share"))
    db_session.commit()


    fake_result = pipeline.NudgeResult(type="suggestion", message="Try that new ramen place tonight!")
    with patch.object(pipeline, "call_llm", return_value=fake_result) as mocked:
        result = pipeline.generate_daily_nudge(db_session, user.id)


    assert result["type"] == "suggestion"
    assert "ramen" in mocked.call_args.kwargs["user_prompt"]
    assert "Free evening" in mocked.call_args.kwargs["user_prompt"]




def test_nudge_asks_question_when_no_free_slot_or_taste_trait(db_session, user):
    fake_result = pipeline.NudgeResult(type="question", message="What's your go-to weekend activity?")
    with patch.object(pipeline, "call_llm", return_value=fake_result) as mocked:
        result = pipeline.generate_daily_nudge(db_session, user.id)


    assert result["type"] == "question"
    assert "Mode: question" in mocked.call_args.kwargs["user_prompt"]



