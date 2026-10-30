"""Unit tests for app/services.py: token encryption, demo-user bootstrapping,
trait retrieval, and demo data loading.
"""
from unittest.mock import patch
import pytest


from app import services
from app.connectors import all_providers
from app.db import BlockedTrait, Connection, EmailIngestion, EmailProcessingLease, Fact, RawItem, Trait, User
from app.demo_seed_data import DEMO_SEED_FACTS, DEMO_SEED_TRAITS




def test_tavily_uses_only_public_query_and_bounded_basic_search(tavily_http):
    import json
    requests = tavily_http({"results":[{"title":"Public sports guide","url":"https://example.org/sports","content":"Public facts."}]})
    result = services.search_public_web("  public sports guide  ")
    assert result[0].title == "Public sports guide"
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["query"] == "public sports guide" and payload["max_results"] == 3
    assert payload["search_depth"] == "basic" and not payload["auto_parameters"]
    assert payload["include_raw_content"] is False and payload["include_answer"] is False
    assert requests[0].url == "https://api.tavily.com/search"
    assert requests[0].headers["authorization"] == "Bearer isolated-tavily-test-key"
    assert "api_key" not in payload and "profile" not in payload




@pytest.mark.parametrize("name",["TAVIL_API_KEY","TAVILY_API_KEY","tavily_api_key"])
def test_tavily_settings_accept_key_aliases(name):
    from app.config import Settings
    settings = Settings(_env_file=None,**{name:"isolated-alias-key"})
    assert settings.tavily_api_key == "isolated-alias-key"
    assert "isolated-alias-key" not in repr(settings)




@pytest.mark.parametrize("query",["","   ","x"*301])
def test_tavily_rejects_invalid_query_before_configuration_or_network(query):
    with patch.object(services,"get_settings") as settings:
        with pytest.raises(services.WebSearchError) as error:
            services.search_public_web(query)
    assert error.value.status_code == 422
    settings.assert_not_called()




@pytest.mark.parametrize("status,expected",[
    (401,"authentication"),(403,"authentication"),(429,"credit limit"),(432,"credit limit"),(433,"credit limit"),
    (500,"search failed"),(301,"search failed"),
])
def test_tavily_errors_do_not_leak_provider_details_or_retry(tavily_http,status,expected):
    requests = tavily_http({"detail":"PRIVATE_PROVIDER_BODY"},status=status)
    with pytest.raises(services.WebSearchError,match=expected) as error:
        services.search_public_web("public topic")
    assert len(requests) == 1 and error.value.status_code == 502
    assert "PRIVATE_PROVIDER_BODY" not in str(error.value)




@pytest.mark.parametrize("body",[
    {},{"results":None},{"results":[{"title":"Missing snippet","url":"https://example.org"}]},
    {"results":[{"title":"Bad link","url":"javascript:alert(1)","content":"text"}]},
    {"results":[{"title":"Bad link","url":"https://user:password@example.org","content":"text"}]},
    {"results":[{"title":"Bad link","url":"https://example.org:invalid/","content":"text"}]},
    {"results":[{"title":"Bad link","url":"https://example.org/a b","content":"text"}]},
    {"results":[{"title":"Extra","url":"https://example.org","content":"text"}]*4},
])
def test_tavily_rejects_malformed_or_unsafe_sources(tavily_http,body):
    requests = tavily_http(body)
    with pytest.raises(services.WebSearchError,match="invalid search results"):
        services.search_public_web("public topic")
    assert len(requests) == 1




def test_tavily_bounds_snippets_and_handles_network_and_json_errors(tavily_http):
    requests = tavily_http({"results":[{"title":"T"*1000,"url":"https://example.org/guide","content":"S"*5000}]})
    result = services.search_public_web("public topic")
    assert len(result[0].title) == 160 and len(result[0].content) == 1200
    assert "content" not in result[0].source()
    assert len(requests) == 1
    requests = tavily_http({},network_error=True)
    with pytest.raises(services.WebSearchError,match="could not be reached"):
        services.search_public_web("public topic")
    assert len(requests) == 1
    tavily_http({})
    with patch("app.services.httpx.Response.json",side_effect=ValueError("PRIVATE_PROVIDER_BODY")):
        with pytest.raises(services.WebSearchError,match="invalid search results"):
            services.search_public_web("public topic")




def test_calendar_sync_is_free_idempotent_and_preserves_dismissal(db_session, user, calendar_event, calendar_sync):
    from datetime import datetime, timezone
    from app.db import CalendarLearning, ModelCall, TodayItem
    event = calendar_event()
    result = calendar_sync([event])
    assert result["events_fetched"] == result["raw_items_inserted"] == 1
    assert calendar_sync([event])["raw_items_inserted"] == 0
    assert db_session.query(RawItem).one().processed
    assert db_session.query(CalendarLearning).one().status == "pending"
    assert db_session.query(Fact).count() == db_session.query(ModelCall).count() == 0
    with patch.object(services,"_today_now",return_value=datetime(2026,10,30,6,tzinfo=timezone.utc)):
        view = services.today_items(db_session,user.id,"Asia/Kolkata")
    assert view["reminder_count"] == 1
    assert view["items"][0]["source"] == "gcal" and not view["items"][0]["all_day"]
    row = db_session.query(TodayItem).one()
    row.status = "dismissed"
    db_session.commit()
    calendar_sync([calendar_event(summary="Badminton rescheduled",start={"dateTime":"2026-10-30T17:00:00+05:30"},
                                  end={"dateTime":"2026-10-30T18:00:00+05:30"})])
    assert row.status == "dismissed" and row.title == "Badminton rescheduled"




def test_calendar_snapshot_removal_partial_sync_and_cancellation(db_session, user, calendar_event, calendar_sync):
    from app.db import TodayItem
    calendar_sync([calendar_event("one"),calendar_event("two")])
    calendar_sync([calendar_event("one")],complete=False)
    assert db_session.query(TodayItem).filter_by(status="pending").count() == 2
    calendar_sync([calendar_event("one")])
    assert db_session.query(TodayItem).filter_by(status="pending").count() == 1
    calendar_sync([{"id":"one","status":"cancelled"}])
    assert db_session.query(TodayItem).filter_by(status="pending").count() == 0




def test_calendar_all_day_end_exclusive_and_declined_events(db_session,user,calendar_event,calendar_sync):
    from datetime import datetime, timezone
    calendar_sync([
        calendar_event("holiday",summary="Holiday",start={"date":"2026-10-29"},end={"date":"2026-11-01"}),
        calendar_event("declined",attendees=[{"self":True,"responseStatus":"declined"}]),
        calendar_event("tentative",status="tentative"),
    ])
    with patch.object(services,"_today_now",return_value=datetime(2026,10,30,6,tzinfo=timezone.utc)):
        view = services.today_items(db_session,user.id,"Asia/Kolkata")
        assert view["reminder_count"] == 1 and view["items"][0]["all_day"]
        assert view["items"][0]["at"] is None
    with patch.object(services,"_today_now",return_value=datetime(2026,11,1,6,tzinfo=timezone.utc)):
        assert services.today_items(db_session,user.id,"Asia/Kolkata")["items"] == []




def test_calendar_learning_batches_usage_private_patterns_and_cache(db_session,user,calendar_event,calendar_sync,calendar_model):
    from app.db import CalendarLearning, ModelCall
    calendar_sync([
        *[calendar_event(f"sport{i}") for i in range(3)],
        calendar_event("work",summary="Client project meeting"),
        calendar_event("study",summary="Study for exam"),
        calendar_event("ignored",summary="Private appointment"),
    ])
    assert services.process_calendar_learning(db_session,user.id)["events_checked"] == 5
    assert calendar_model.call_count == 1
    assert services.process_calendar_learning(db_session,user.id)["events_checked"] == 1
    assert services.process_calendar_learning(db_session,user.id)["events_checked"] == 0
    assert calendar_model.call_count == db_session.query(ModelCall).count() == 2
    assert {row.task for row in db_session.query(ModelCall)} == {"calendar_learning"}
    assert {row.model for row in db_session.query(ModelCall)} == {"super"}
    assert calendar_model.call_args.kwargs["max_tokens"] == 2048
    assert db_session.query(CalendarLearning).filter_by(status="ignored").count() == 1
    assert db_session.query(Fact).count() == 5
    assert all(fact.synthesized and fact.privacy_tier == "private" for fact in db_session.query(Fact))
    traits = {trait.key:trait for trait in db_session.query(Trait)}
    assert set(traits) == {"calendar_badminton","calendar_work","calendar_study"}
    assert all(trait.privacy_tier == "private" for trait in traits.values())
    assert traits["calendar_badminton"].evidence_count == 3
    assert traits["calendar_badminton"].value == "Schedules badminton on Friday, in the afternoon."
    assert "Client project" not in traits["calendar_work"].value
    assert "attendance" in traits["calendar_work"].summary
    assert db_session.query(EmailProcessingLease).count() == 0




def test_calendar_changes_withdraw_learned_evidence_and_privatize_reviewed_snapshot(db_session,user,calendar_event,calendar_sync,calendar_model):
    calendar_sync([calendar_event("one"),calendar_event("two")])
    services.process_calendar_learning(db_session,user.id)
    trait = db_session.query(Trait).one()
    original = trait.value
    trait.user_overridden, trait.privacy_tier = True,"share"
    db_session.commit()
    calendar_sync([{"id":"one","status":"cancelled"},calendar_event("two",summary="Badminton moved")])
    assert db_session.query(Fact).count() == 0
    assert trait.value == original and trait.evidence_count == 0 and trait.privacy_tier == "private"
    assert services.process_calendar_learning(db_session,user.id)["events_checked"] == 1
    assert trait.value == original and trait.evidence_count == 0
    assert calendar_model.call_count == 2
    assert db_session.query(Fact).count() == 1




def test_calendar_ignored_events_stay_ignored_on_identical_sync(db_session,user,calendar_event,calendar_sync,calendar_model):
    event = calendar_event(summary="Private appointment")
    calendar_sync([event])
    services.process_calendar_learning(db_session,user.id)
    calendar_sync([event])
    assert services.process_calendar_learning(db_session,user.id)["events_checked"] == 0
    assert calendar_model.call_count == 1




def test_calendar_learning_failure_is_saved_and_budget_prevents_calls(db_session,user,calendar_event,calendar_sync,calendar_model,gmail_config):
    from types import SimpleNamespace
    from app.db import CalendarLearning
    calendar_sync([calendar_event()])
    calendar_model.side_effect = None
    calendar_model.return_value = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop",message=SimpleNamespace(content="invalid JSON"))],
        usage=SimpleNamespace(prompt_tokens=10,completion_tokens=5),
    )
    for _ in range(2):
        result = services.process_calendar_learning(db_session,user.id)
        assert result["results"][0]["status"] == "failed"
    assert calendar_model.call_count == 1 and db_session.query(CalendarLearning).one().candidate
    assert db_session.query(Fact).count() == 0
    calendar_sync([calendar_event(summary="Badminton updated")])
    gmail_config.daily_spend_cap_usd = 0
    result = services.process_calendar_learning(db_session,user.id)
    assert "budget" in result["results"][0]["error"]
    assert calendar_model.call_count == 1 and db_session.query(EmailProcessingLease).count() == 0




def test_calendar_sync_refreshes_once_and_does_not_mutate_events_on_failure(db_session,user,calendar_event,calendar_sync):
    from datetime import datetime, timedelta, timezone
    from app.connectors import ConnectorError, TokenBundle
    from app.db import CalendarAccount
    calendar_sync([calendar_event()])
    before = db_session.query(RawItem).one().payload_json.copy()
    identity = services.CalendarIdentity(id="calendar@example.com",timeZone="Asia/Kolkata")
    tokens = TokenBundle("new-access","rotated-refresh",datetime.now(timezone.utc)+timedelta(hours=1))
    with patch.object(services.GoogleCalendarConnector,"primary_calendar",return_value=identity), patch.object(
        services.GoogleCalendarConnector,"fetch_window",side_effect=[ConnectorError("Expired",reconnect=True),ConnectorError("Malformed")],
    ) as fetch, patch.object(services.GoogleCalendarConnector,"refresh",return_value=tokens) as refresh:
        with pytest.raises(ConnectorError,match="Malformed"):
            services.sync_calendar(db_session,user.id)
    refresh.assert_called_once()
    assert fetch.call_count == 2
    assert db_session.query(RawItem).one().payload_json == before
    assert services.decrypt_token(db_session.query(Connection).one().encrypted_refresh_token) == "rotated-refresh"
    assert db_session.get(CalendarAccount,user.id).calendar_id == "calendar@example.com"




def test_calendar_blocked_patterns_do_not_reappear(db_session,user,calendar_event,calendar_sync,calendar_model):
    db_session.add(BlockedTrait(user_id=user.id,category="fitness",key="calendar_badminton"))
    db_session.commit()
    calendar_sync([calendar_event()])
    services.process_calendar_learning(db_session,user.id)
    assert db_session.query(Fact).count() == 1 and db_session.query(Trait).count() == 0




def test_today_scan_is_separate_from_profiles_and_cached(db_session, user, today_email, today_candidate):
    from datetime import datetime, timezone
    item = today_email()
    with patch.object(services, "_today_now", return_value=datetime(2026, 10, 30, 6, tzinfo=timezone.utc)), patch.object(
        services, "extract_today_candidates", return_value=[today_candidate()],
    ) as extraction:
        result = services.process_today_emails(db_session, user.id, "Asia/Kolkata")
        assert result["emails_checked"] == 1
        result = services.process_today_emails(db_session, user.id, "Asia/Kolkata")
        assert result["emails_checked"] == 0
        extraction.assert_called_once()
        view = services.today_items(db_session, user.id, "Asia/Kolkata")
    assert view["action_count"] == 1 and view["reminder_count"] == 0
    assert view["items"][0]["title"] == "Review proposal draft"
    assert item.processed and db_session.query(Fact).count() == db_session.query(Trait).count() == 0




def test_today_filters_timezone_future_past_and_snoozed(db_session, user, today_email, today_candidate):
    from datetime import datetime, timezone
    from app.db import TodayItem
    today_email()
    now = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
    candidates = [
        today_candidate(),
        today_candidate(kind="reminder", title="Badminton game", at="2026-10-30T15:00:00+05:30"),
        today_candidate(kind="reminder", title="Earlier meeting", at="2026-10-30T10:00:00+05:30"),
        today_candidate(day="2026-10-31", title="Submit assignment tomorrow"),
    ]
    with patch.object(services, "_today_now", return_value=now), patch.object(
        services, "extract_today_candidates", return_value=candidates,
    ):
        services.process_today_emails(db_session, user.id, "Asia/Kolkata")
        view = services.today_items(db_session, user.id, "Asia/Kolkata")
        assert view["action_count"] == view["reminder_count"] == 1
        assert len(view["items"]) == 2
        assert services.today_items(db_session, user.id, "America/Los_Angeles")["items"] == []
        action = next(item for item in view["items"] if item["kind"] == "action")
        row = db_session.get(TodayItem, action["id"])
        row.snoozed_until = datetime(2026, 10, 30, 6, 30, tzinfo=timezone.utc)
        db_session.commit()
        assert services.today_items(db_session, user.id, "Asia/Kolkata")["action_count"] == 0
    with patch.object(services, "_today_now", return_value=datetime(2026, 10, 30, 6, 31, tzinfo=timezone.utc)):
        assert services.today_items(db_session, user.id, "Asia/Kolkata")["action_count"] == 1




def test_today_batch_limit_partial_failures_and_budget(db_session, user, today_email, today_candidate):
    from app.pipeline import BudgetExceededError, EmailProcessingError
    for _ in range(6):
        today_email()
    with patch.object(services, "extract_today_candidates", side_effect=[
        EmailProcessingError("Unsupported evidence"), *[[today_candidate()] for _ in range(4)],
    ]) as extraction:
        result = services.process_today_emails(db_session, user.id, "UTC")
    assert result["emails_checked"] == 5 and extraction.call_count == 5
    assert result["results"][0]["status"] == "failed"
    with patch.object(services, "extract_today_candidates", side_effect=BudgetExceededError("Budget exhausted")) as extraction:
        assert services.process_today_emails(db_session, user.id, "UTC")["emails_checked"] == 1
        extraction.assert_called_once()
    assert db_session.query(EmailProcessingLease).count() == 0




def test_today_reference_updates_dedupe_and_preserve_manual_completion(db_session, user, today_email, today_candidate):
    from app.db import TodayItem
    candidate = today_candidate(reference={"value":"TASK-1","quote":"Task ID: TASK-1"})
    today_email()
    with patch.object(services, "extract_today_candidates", return_value=[candidate]):
        services.process_today_emails(db_session, user.id, "UTC")
        row = db_session.query(TodayItem).one()
        row.status = "done"
        db_session.commit()
        today_email()
        services.process_today_emails(db_session, user.id, "UTC")
    assert db_session.query(TodayItem).count() == 1
    assert row.status == "done" and len(row.source_item_ids) == 2
    today_email()
    with patch.object(services, "extract_today_candidates", return_value=[{**candidate, "operation":"cancel"}]):
        services.process_today_emails(db_session, user.id, "UTC")
    assert row.status == "cancelled"
    today_email()
    with patch.object(services, "extract_today_candidates", return_value=[today_candidate(operation="cancel")]):
        result = services.process_today_emails(db_session, user.id, "UTC")
    assert result["results"][0]["unmatched_updates"] == 1
    assert db_session.query(TodayItem).count() == 1




def test_today_newer_cancellation_wins_even_when_older_confirmation_scanned_later(db_session, user, today_email, today_candidate):
    from datetime import datetime, timezone
    from app.db import TodayItem
    candidate = today_candidate(kind="reminder", title="Badminton game", at="2026-10-30T15:00:00+05:30",
                                reference={"value":"BOOK-1","quote":"Booking ID: BOOK-1"})
    today_email(received=datetime(2026, 10, 30, 7, tzinfo=timezone.utc))
    with patch.object(services, "extract_today_candidates", return_value=[{**candidate,"operation":"cancel"}]):
        services.process_today_emails(db_session, user.id, "Asia/Kolkata")
    today_email(received=datetime(2026, 10, 30, 6, tzinfo=timezone.utc))
    with patch.object(services, "extract_today_candidates", return_value=[candidate]):
        services.process_today_emails(db_session, user.id, "Asia/Kolkata")
    row = db_session.query(TodayItem).one()
    assert row.status == "cancelled" and len(row.source_item_ids) == 2
    with patch.object(services, "_today_now", return_value=datetime(2026, 10, 30, 6, tzinfo=timezone.utc)):
        assert services.today_items(db_session, user.id, "Asia/Kolkata")["reminder_count"] == 0




def test_today_future_action_appears_on_its_day_without_another_model_call(db_session, user, today_email, today_candidate):
    from datetime import datetime, timezone
    today_email()
    with patch.object(services, "extract_today_candidates", return_value=[today_candidate(day="2026-10-31")]) as extraction:
        services.process_today_emails(db_session, user.id, "Asia/Kolkata")
        with patch.object(services, "_today_now", return_value=datetime(2026, 10, 30, 6, tzinfo=timezone.utc)):
            assert services.today_items(db_session, user.id, "Asia/Kolkata")["action_count"] == 0
        with patch.object(services, "_today_now", return_value=datetime(2026, 10, 31, 6, tzinfo=timezone.utc)):
            assert services.today_items(db_session, user.id, "Asia/Kolkata")["action_count"] == 1
        extraction.assert_called_once()




def test_today_rejects_bad_import_source_and_changed_lease_without_flagging_items(db_session, user, today_email, today_candidate):
    from app.pipeline import EmailProcessingError
    from app.db import TodayItem
    item = today_email()
    item.payload_json = {"source":"sample"}
    db_session.commit()
    with patch.object(services, "extract_today_candidates") as extraction:
        with pytest.raises(EmailProcessingError, match="Invalid live source"):
            services.process_today_emails(db_session, user.id, "UTC")
        extraction.assert_not_called()
    item.payload_json = {"source":"gmail_live"}
    db_session.commit()
    def lose_lease(*args):
        db_session.query(EmailProcessingLease).delete()
        db_session.commit()
        return [today_candidate()]
    with patch.object(services, "extract_today_candidates", side_effect=lose_lease):
        with pytest.raises(EmailProcessingError, match="ownership changed"):
            services.process_today_emails(db_session, user.id, "UTC")
    assert db_session.query(TodayItem).count() == 0




# ---- crypto ----




def _live_emails(db, user, count):
    items = [RawItem(user_id=user.id, provider="gmail", external_id=f"gmail_live:mailbox:{index}",
                     item_type="email_receipt", payload_json={"source":"gmail_live"}) for index in range(count)]
    db.add_all(items)
    db.commit()
    return items




def _food_event(dish="Dosa", restaurant="Cafe A", order_id=None):
    def field(value):
        return {"value":value, "quote":value} if value is not None else None
    return {"dishes":[field(dish)], "restaurant":field(restaurant), "order_id":field(order_id)}




def _classify_food(db, user_id, items):
    return {item.id:"food_order" for item in items}




def _record_activity(db, user, event, kind="food_order"):
    import uuid
    identity = str(uuid.uuid4())
    item = RawItem(user_id=user.id, provider="gmail", external_id=f"gmail_live:test:{identity}",
                   item_type="email_receipt", payload_json={"source":"gmail_live"}, processed=True)
    fact = Fact(user_id=user.id, category="food" if kind == "food_order" else "fitness",
                statement="Test evidence", source_item_ids=[], model_used="super")
    db.add_all([item, fact])
    db.flush()
    fact.source_item_ids = [item.id]
    db.add(EmailIngestion(raw_item_id=item.id, user_id=user.id, kind=kind, status="extracted",
                          event=event, event_key=identity, fact_id=fact.id))
    db.flush()
    return fact




def _preferences(db, user):
    services.refresh_activity_traits(db, user.id)
    db.flush()
    return {trait.key: trait for trait in db.query(Trait).filter_by(user_id=user.id)}




def test_preferences_merge_biryani_variants_and_reconcile_relative_frequency(db_session, user):
    _record_activity(db_session, user, _food_event("Chicken Dum Biryani", "Biryani House"))
    _record_activity(db_session, user, _food_event("Chicken Masaledar Biryani", "Biryani House"))
    _record_activity(db_session, user, _food_event("Andhra Paneer Biryani Regular (Serves 1)", "Cafe B"))
    cards = _preferences(db_session, user)
    biryani = cards["preference_food_biryani"]
    original_id = biryani.id
    assert "Most often orders biryani" in biryani.value
    assert "especially chicken biryani" in biryani.value
    assert "occasionally also tries paneer biryani" in biryani.value
    assert "Masaledar" not in biryani.value and "(1)" not in biryani.value
    assert biryani.evidence_count == 3 and biryani.privacy_tier == "private"
    assert len(cards) == 2
    for index in range(10):
        _record_activity(db_session, user, _food_event("Veg Hakka Noodles" if index % 2 else "Gobi Manchurian", "Wok House"))
    cards = _preferences(db_session, user)
    assert cards["preference_food_biryani"].id == original_id
    assert cards["preference_food_chinese"].value.startswith("Most often orders Chinese food")
    assert cards["preference_food_biryani"].value.startswith("Also orders biryani")
    assert len(cards) == 4
    assert "Wok House" in cards["preference_restaurants_chinese"].value
    assert "Chinese" in cards["preference_restaurants_chinese"].value
    assert all(len(trait.value) <= 320 for trait in cards.values())




def test_preferences_count_orders_not_items_and_keep_unknown_menu_names_in_evidence(db_session, user):
    order = _food_event("Chicken Peri Peri Burger")
    order["dishes"] *= 5
    _record_activity(db_session, user, order)
    _record_activity(db_session, user, _food_event("Truffles Rooster"))
    cards = _preferences(db_session, user)
    assert cards["preference_food_burgers_sandwiches"].evidence_count == 1
    assert "chicken burgers" in cards["preference_food_burgers_sandwiches"].value
    assert "Truffles Rooster" not in " ".join(trait.value for trait in cards.values())
    assert all("Most often" not in trait.value for trait in cards.values())




def test_preferences_sport_aliases_venues_and_frequency_shift(db_session, user):
    field = lambda value: {"value":value, "quote":value}
    for sport in ("Badminton", "badminton", " Badminton "):
        _record_activity(db_session, user, {"sport":field(sport), "venue":field("Orchid Academy"),
                                          "order_id":None}, "sports_booking")
    cards = _preferences(db_session, user)
    assert len(cards) == 1
    assert cards["preference_sport_badminton"].value.startswith("Most often books badminton")
    assert "Orchid Academy" in cards["preference_sport_badminton"].value
    for _ in range(6):
        _record_activity(db_session, user, {"sport":field("Box Cricket"), "venue":None,
                                          "order_id":None}, "sports_booking")
    cards = _preferences(db_session, user)
    assert cards["preference_sport_cricket"].value.startswith("Most often books cricket")
    assert cards["preference_sport_badminton"].value.startswith("Also books badminton")




def test_preferences_keep_snapshot_blocks_and_remove_stale_groups(db_session, user):
    fact = _record_activity(db_session, user, _food_event("Chicken Biryani"))
    cards = _preferences(db_session, user)
    saved = cards["preference_food_biryani"]
    saved.value, saved.user_overridden, saved.privacy_tier = "My chosen preference", True, "ask"
    db_session.add(BlockedTrait(user_id=user.id, category="food", key="preference_restaurants_biryani"))
    db_session.delete(cards["preference_restaurants_biryani"])
    db_session.flush()
    _record_activity(db_session, user, _food_event("Paneer Biryani"))
    cards = _preferences(db_session, user)
    assert saved.value == "My chosen preference" and saved.privacy_tier == "ask"
    assert "preference_restaurants_biryani" not in cards
    for state in db_session.query(EmailIngestion).all():
        state.status = "removed"
    db_session.flush()
    cards = _preferences(db_session, user)
    assert set(cards) == {"preference_food_biryani"}
    assert fact.id in saved.supporting_fact_ids




def test_preferences_use_model_concepts_and_do_not_create_false_winner_on_ties(db_session, user):
    event = _food_event("House Special", "Wok House")
    event["concepts"] = [{"dish":"House Special","group":"chinese","family":"noodles",
                         "quote":"House Special - noodles"}]
    for _ in range(2):
        _record_activity(db_session, user, event)
        _record_activity(db_session, user, _food_event("Chicken Biryani"))
    cards = _preferences(db_session, user)
    assert "noodles" in cards["preference_food_chinese"].value
    assert all("Most often" not in card.value for card in cards.values())
    assert all(card.evidence_count == 2 for card in cards.values())




def test_preferences_occasional_group_and_bounded_place_examples(db_session, user):
    for _ in range(10):
        _record_activity(db_session, user, _food_event("Hakka Noodles"))
    for index in range(2):
        _record_activity(db_session, user, _food_event("Chicken Biryani", str(index) + "A" * 119))
    cards = _preferences(db_session, user)
    assert cards["preference_food_biryani"].value.startswith("Occasionally orders biryani")
    assert all(len(card.value) <= 320 for card in cards.values())




def test_restaurant_preference_frequency_uses_only_identified_restaurant_orders(db_session, user):
    for _ in range(5):
        _record_activity(db_session, user, _food_event("Chicken Biryani", None))
    for _ in range(2):
        _record_activity(db_session, user, _food_event("Hakka Noodles", "Wok House"))
    _record_activity(db_session, user, _food_event("Chicken Biryani", "Biryani House"))
    cards = _preferences(db_session, user)
    assert cards["preference_food_biryani"].value.startswith("Most often orders biryani")
    assert cards["preference_restaurants_chinese"].value.startswith("Most often orders Chinese food from")
    assert cards["preference_restaurants_biryani"].value.startswith("Has ordered biryani from")




def test_unknown_dishes_stay_in_evidence_without_creating_meaningless_cards(db_session, user):
    _record_activity(db_session, user, _food_event("Truffles Rooster"))
    assert _preferences(db_session, user) == {}
    assert services.activity_rankings(db_session, user.id)["foods"][0]["name"] == "Truffles Rooster"




def test_legacy_venue_deletion_suppresses_replacement_with_venue_content(db_session, user):
    _record_activity(db_session, user, {"sport":{"value":"Badminton","quote":"Badminton"},
                                      "venue":{"value":"Arena","quote":"Arena"}, "order_id":None}, "sports_booking")
    db_session.add(BlockedTrait(user_id=user.id, category="fitness", key="activity_venues"))
    db_session.flush()
    assert _preferences(db_session, user) == {}




def test_batch_limit_and_aggregate_from_first_order(db_session, user):
    db_session.autoflush = False
    items = _live_emails(db_session, user, 6)
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food) as classify, patch.object(
        services, "extract_email_event", return_value=_food_event(),
    ) as extract:
        result = services.process_email_batch(db_session, user.id)
    assert len(result["results"]) == 5
    assert len(classify.call_args.args[2]) == 5 and extract.call_count == 5
    assert db_session.query(EmailIngestion).count() == 5
    assert db_session.query(Trait).count() == 2
    rankings = services.activity_rankings(db_session, user.id)
    assert rankings["foods"][0]["count"] == 5
    assert rankings["restaurants"][0]["children"] == [{"name":"Dosa","count":5}]
    assert db_session.query(EmailProcessingLease).count() == 0
    assert all(trait.privacy_tier == "private" for trait in db_session.query(Trait))
    assert db_session.query(RawItem).filter_by(processed=False).count() == 1
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food), patch.object(
        services, "extract_email_event", return_value=_food_event(" dosa ", "CAFE A"),
    ):
        services.process_email_batch(db_session, user.id)
    assert services.activity_rankings(db_session, user.id)["foods"][0]["count"] == 6
    assert db_session.query(Trait).count() == 2




def test_batch_deduplicates_merchant_order_id_and_keeps_source_evidence(db_session, user):
    _live_emails(db_session, user, 3)
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food), patch.object(
        services, "extract_email_event", side_effect=[_food_event(order_id="123"), _food_event(order_id="123"),
                                                      _food_event(restaurant="Cafe B", order_id="123")],
    ):
        result = services.process_email_batch(db_session, user.id)
    assert [entry["status"] for entry in result["results"]].count("duplicate") == 1
    assert db_session.query(Fact).count() == 2
    assert max(len(fact.source_item_ids) for fact in db_session.query(Fact)) == 2
    assert services.activity_rankings(db_session, user.id)["foods"][0]["count"] == 2
    with patch.object(services, "classify_imported_emails") as call:
        assert services.process_email_batch(db_session, user.id)["results"] == []
        call.assert_not_called()




def test_batch_partial_failure_reuses_classification_and_skips_promotions(db_session, user):
    from app.pipeline import EmailProcessingError
    items = _live_emails(db_session, user, 4)
    labels = dict(zip(sorted(item.id for item in items), ["food_order","food_order","promotion","unknown"]))
    with patch.object(services, "classify_imported_emails", return_value=labels), patch.object(
        services, "extract_email_event", side_effect=[_food_event(), EmailProcessingError("Synthetic failure")],
    ):
        result = services.process_email_batch(db_session, user.id)
    assert {entry["status"] for entry in result["results"]} == {"extracted","failed","skipped","review"}
    assert db_session.query(Fact).count() == 1
    assert db_session.query(RawItem).filter_by(processed=False).count() == 1
    with patch.object(services, "classify_imported_emails") as classify, patch.object(
        services, "extract_email_event", return_value=_food_event(),
    ):
        services.process_email_batch(db_session, user.id)
        classify.assert_not_called()
    assert services.activity_rankings(db_session, user.id)["foods"][0]["count"] == 2




def test_batch_invalid_classification_releases_lease_without_processing(db_session, user):
    from app.pipeline import EmailProcessingError
    _live_emails(db_session, user, 1)
    with patch.object(services, "classify_imported_emails", side_effect=EmailProcessingError("invalid")):
        with pytest.raises(EmailProcessingError):
            services.process_email_batch(db_session, user.id)
    assert db_session.query(EmailProcessingLease).count() == 0
    assert db_session.query(EmailIngestion).count() == 0
    assert db_session.query(RawItem).filter_by(processed=False).count() == 1




def test_activity_summary_honors_block_and_user_snapshot(db_session, user):
    _live_emails(db_session, user, 1)
    db_session.add_all([
        BlockedTrait(user_id=user.id, category="food", key="activity_restaurants"),
        Trait(user_id=user.id, category="food", key="activity_foods", value="My reviewed snapshot",
              privacy_tier="ask", user_overridden=True),
    ])
    db_session.commit()
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food), patch.object(
        services, "extract_email_event", return_value=_food_event(),
    ):
        services.process_email_batch(db_session, user.id)
    assert db_session.query(Trait).count() == 1
    assert db_session.query(Trait).one().value == "My reviewed snapshot"
    assert db_session.query(Trait).one().privacy_tier == "ask"
    assert services.activity_rankings(db_session, user.id)["foods"][0]["count"] == 1




def test_activity_counts_twenty_orders_in_two_summary_cards(db_session, user):
    _live_emails(db_session, user, 20)
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food), patch.object(
        services, "extract_email_event", return_value=_food_event(),
    ):
        for _ in range(4):
            services.process_email_batch(db_session, user.id)
    assert db_session.query(Fact).count() == 20
    assert db_session.query(Trait).count() == 2
    assert services.activity_rankings(db_session, user.id)["foods"][0]["count"] == 20




def test_duplicate_receipts_merge_dishes_without_double_counting(db_session, user):
    _live_emails(db_session, user, 2)
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food), patch.object(
        services, "extract_email_event", side_effect=[_food_event("Dosa", order_id="A"), _food_event("Idli", order_id="A")],
    ):
        services.process_email_batch(db_session, user.id)
    rankings = services.activity_rankings(db_session, user.id)
    assert [entry["count"] for entry in rankings["foods"]] == [1, 1]
    assert rankings["restaurants"][0]["count"] == 1
    assert len(db_session.query(Fact).one().source_item_ids) == 2




def test_sports_travel_and_same_dish_in_different_restaurants(db_session, user):
    items = _live_emails(db_session, user, 5)
    ordered = sorted(items, key=lambda item:item.id)
    labels = dict(zip([item.id for item in ordered], ["sports_booking","sports_booking","travel_booking","food_order","food_order"]))
    field = lambda value: {"value":value, "quote":value}
    sport = {"sport":field("Badminton"), "venue":field("Arena"), "order_id":None}
    with patch.object(services, "classify_imported_emails", return_value=labels), patch.object(
        services, "extract_email_event", side_effect=[sport, sport, {"destination":field("Paris"),"order_id":None},
                                                     _food_event(), _food_event(restaurant="Cafe B")],
    ):
        services.process_email_batch(db_session, user.id)
    ranks = services.activity_rankings(db_session, user.id)
    assert ranks["sports"][0]["count"] == 2 and ranks["venues"][0]["children"][0]["count"] == 2
    assert ranks["destinations"][0]["name"] == "Paris"
    assert ranks["foods"][0]["count"] == 2 and len(ranks["restaurants"]) == 2
    assert db_session.query(Trait).filter_by(category="fitness").count() == 1




def test_batch_budget_failure_saves_classification_not_activity(db_session, user):
    from app.pipeline import BudgetExceededError
    _live_emails(db_session, user, 2)
    with patch.object(services, "classify_imported_emails", side_effect=_classify_food), patch.object(
        services, "extract_email_event", side_effect=BudgetExceededError("Insufficient budget"),
    ) as extraction:
        result = services.process_email_batch(db_session, user.id)
    assert len(result["results"]) == 1 and result["results"][0]["status"] == "failed"
    extraction.assert_called_once()
    assert db_session.query(EmailIngestion).count() == 2
    assert db_session.query(RawItem).filter_by(processed=False).count() == 2
    assert db_session.query(Fact).count() == 0




def test_encrypt_decrypt_roundtrip():
    original = "super-secret-oauth-token"
    encrypted = services.encrypt_token(original)


    assert encrypted != original
    assert services.decrypt_token(encrypted) == original




# ---- demo user ----




def test_get_or_create_demo_user_creates_once_then_reuses(db_session):
    first = services.get_or_create_demo_user(db_session)
    second = services.get_or_create_demo_user(db_session)


    assert first.id == second.id
    assert db_session.query(User).count() == 1




# ---- trait retrieval ----




def _make_trait(db_session, user_id, category, confidence, tier="share", key=None):
    trait = Trait(
        user_id=user_id, category=category, key=key or f"{category}_trait", value=f"value for {category}",
        confidence=confidence, privacy_tier=tier,
    )
    db_session.add(trait)
    db_session.commit()
    return trait




def test_categories_for_query_matches_keywords():
    assert "food" in services.categories_for_query("What restaurant should I try for dinner?")
    assert "music" in services.categories_for_query("recommend me a new song")
    assert services.categories_for_query("asdkjasd qwoeiqwe") == []




def test_retrieve_relevant_traits_prefers_matched_category(db_session, user):
    _make_trait(db_session, user.id, "food", confidence=0.9)
    _make_trait(db_session, user.id, "music", confidence=0.95)


    results = services.retrieve_relevant_traits(db_session, user.id, "what should I eat for dinner?")


    assert len(results) == 1
    assert results[0].category == "food"




def test_retrieve_relevant_traits_falls_back_to_top_confidence(db_session, user):
    _make_trait(db_session, user.id, "food", confidence=0.4)
    _make_trait(db_session, user.id, "music", confidence=0.9)


    results = services.retrieve_relevant_traits(db_session, user.id, "unrelated gibberish query")


    assert results[0].category == "music"  # highest confidence wins when no keyword matches




def test_retrieve_relevant_traits_excludes_private_tier_by_default(db_session, user):
    _make_trait(db_session, user.id, "routine", confidence=0.9, tier="private")


    results = services.retrieve_relevant_traits(db_session, user.id, "what's my morning routine?")


    assert results == []




# ---- demo loader ----




def test_load_sample_data_for_provider_is_idempotent(db_session, user):
    first = services.load_sample_data_for_provider(db_session, user.id, "gmail")
    second = services.load_sample_data_for_provider(db_session, user.id, "gmail")


    assert first > 0
    assert second == 0  # no duplicates inserted on reload
    assert db_session.query(RawItem).filter(RawItem.provider == "gmail").count() == first


    connection = db_session.query(Connection).filter(Connection.user_id == user.id, Connection.provider == "gmail").one()
    assert connection.status == "demo"




def test_load_all_demo_data_loads_every_provider_and_seeds_profile_with_zero_llm_cost(db_session, user):
    with patch.object(
        services,
        "seed_demo_profile_for_user",
        return_value={"facts_created": 0, "traits_upserted": 0},
    ) as mocked_seed:
        result = services.load_all_demo_data(db_session, user.id)


    assert set(result["inserted_by_provider"].keys()) == set(all_providers())
    assert all(count > 0 for count in result["inserted_by_provider"].values())
    mocked_seed.assert_called_once_with(db_session, user.id)




# ---- instant demo seed (Plan B) ----




def _load_all_raw_items(db_session, user):
    for provider in all_providers():
        services.load_sample_data_for_provider(db_session, user.id, provider)




def test_seed_demo_profile_makes_zero_llm_calls(db_session, user):
    """The whole point of the seed path is that it never touches pipeline.call_llm."""
    _load_all_raw_items(db_session, user)
    with patch("app.pipeline.call_llm") as mocked_call_llm, patch("app.pipeline.call_llm_stream") as mocked_stream:
        services.seed_demo_profile_for_user(db_session, user.id)


    mocked_call_llm.assert_not_called()
    mocked_stream.assert_not_called()




def test_seed_demo_profile_creates_expected_facts_and_traits(db_session, user):
    _load_all_raw_items(db_session, user)


    result = services.seed_demo_profile_for_user(db_session, user.id)


    assert result["facts_created"] == len(DEMO_SEED_FACTS)
    assert result["traits_upserted"] == len(DEMO_SEED_TRAITS)
    assert db_session.query(Fact).count() == len(DEMO_SEED_FACTS)
    assert db_session.query(Trait).count() == len(DEMO_SEED_TRAITS)




def test_seed_demo_profile_facts_are_untagged_for_later_real_rebuild(db_session, user):
    _load_all_raw_items(db_session, user)
    services.seed_demo_profile_for_user(db_session, user.id)


    facts = db_session.query(Fact).all()
    assert all(f.privacy_tier is None for f in facts)
    assert all(f.model_used == "demo_seed" for f in facts)




def test_seed_demo_profile_links_evidence_to_real_raw_items(db_session, user):
    _load_all_raw_items(db_session, user)
    services.seed_demo_profile_for_user(db_session, user.id)


    raw_item_ids = {row[0] for row in db_session.query(RawItem.id).all()}
    facts = db_session.query(Fact).all()
    assert any(fact.source_item_ids for fact in facts)
    for fact in facts:
        for source_id in fact.source_item_ids:
            assert source_id in raw_item_ids  # evidence points at genuine sample raw items




def test_seed_demo_profile_trait_privacy_tier_matches_category_default(db_session, user):
    _load_all_raw_items(db_session, user)
    services.seed_demo_profile_for_user(db_session, user.id)


    food_trait = db_session.query(Trait).filter(Trait.category == "food", Trait.key == "favorite_cuisine").one()
    assert food_trait.privacy_tier == "share"


    routine_trait = db_session.query(Trait).filter(Trait.category == "routine").first()
    assert routine_trait.privacy_tier == "private"




def test_seed_demo_profile_is_idempotent_on_second_call(db_session, user):
    _load_all_raw_items(db_session, user)


    first = services.seed_demo_profile_for_user(db_session, user.id)
    second = services.seed_demo_profile_for_user(db_session, user.id)


    assert first["facts_created"] == len(DEMO_SEED_FACTS)
    assert second["facts_created"] == 0  # no duplicate facts on reseed
    assert db_session.query(Fact).count() == len(DEMO_SEED_FACTS)
    assert db_session.query(Trait).count() == len(DEMO_SEED_TRAITS)  # upserted, not duplicated




def test_seed_demo_profile_does_not_overwrite_user_overridden_trait(db_session, user):
    _load_all_raw_items(db_session, user)
    services.seed_demo_profile_for_user(db_session, user.id)


    trait = db_session.query(Trait).filter(Trait.category == "food", Trait.key == "favorite_cuisine").one()
    trait.value = "User-edited value"
    trait.user_overridden = True
    db_session.commit()


    services.seed_demo_profile_for_user(db_session, user.id)


    db_session.refresh(trait)
    assert trait.value == "User-edited value"




def test_seed_demo_profile_respects_blocked_trait(db_session, user):
    _load_all_raw_items(db_session, user)
    db_session.add(BlockedTrait(user_id=user.id, category="food", key="favorite_cuisine"))
    db_session.commit()


    services.seed_demo_profile_for_user(db_session, user.id)


    assert db_session.query(Trait).filter(Trait.category == "food", Trait.key == "favorite_cuisine").count() == 0



