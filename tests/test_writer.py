import time

import pytest

from app import config
from app.agent.writer import Writer
from app.examples import ExampleStore
from app.resolve import Resolver
from app.semantic import load_semantic, render_schema_prompt
from tests.fakes import FakeLLM, call


@pytest.fixture(scope="module")
def parts(db):
    return db, Resolver(db), ExampleStore.from_yaml(config.EXAMPLES_PATH), render_schema_prompt(load_semantic())


def make(parts, replies, **kw):
    db, resolver, examples, schema = parts
    llm = FakeLLM(chat_replies=replies)
    return Writer(llm, db, resolver, examples, schema, **kw), llm


def deadline():
    return time.monotonic() + 60


COUNT_SPEC = {"kind": "deals_aggregate", "measures": ["deal_count"],
              "filters": {"year": 2025, "seller_inns": ["975923536"]}}


def test_resolve_then_compile(parts):
    writer, llm = make(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                               call("compile_query", spec=COUNT_SPEC, assumptions=[])])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert res.status == "ok" and res.path == "compiled"
    assert res.result.columns == ["deals_all", "deals_valid"]
    assert "status_scope" in res.splits
    candidates = llm.chat.sent[1][1][0][1]["candidates"]
    assert candidates[0]["id"] == "975923536"


def test_unresolved_inn_is_rejected_then_unanswerable(parts):
    writer, llm = make(parts, [call("compile_query", spec=COUNT_SPEC, assumptions=[]),
                               call("declare_unanswerable", reason="company not found")])
    res = writer.run("Сколько продала компания X?", "ru", deadline())
    assert res.status == "unanswerable"
    error = llm.chat.sent[1][1][0][1]["error"]
    assert "975923536" in error


def test_spec_error_out_of_range_is_fed_back(parts):
    bad = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2021}}
    writer, llm = make(parts, [call("compile_query", spec=bad, assumptions=[]),
                               call("declare_unanswerable", reason="2021 not in data")])
    res = writer.run("Сколько сделок было в 2021?", "ru", deadline())
    assert res.status == "unanswerable"
    assert "outside the data" in llm.chat.sent[1][1][0][1]["error"]


def test_free_sql_fallback(parts):
    sql = "SELECT AVG(delivery_term_days) AS avg_days FROM contracts WHERE contract_form = 'Форвард'"
    writer, _ = make(parts, [call("submit_sql", sql=sql, splits=[], assumptions=[])])
    res = writer.run("Средний срок поставки по форвардам?", "ru", deadline())
    assert res.status == "ok" and res.path == "sql"
    assert res.result.columns == ["avg_days"]


def test_fix_budget(parts):
    bad = {"kind": "deals_aggregate", "measures": ["profit"]}
    writer, _ = make(parts, [call("compile_query", spec=bad, assumptions=[])] * 3, max_fixes=2)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "fix" in res.reason


def test_tool_budget(parts):
    writer, _ = make(parts, [call("get_examples", question="x")] * 4, max_tool_calls=3)
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed" and "tool budget" in res.reason


def test_deadline(parts):
    writer, _ = make(parts, [call("get_examples", question="x")])
    res = writer.run("?", "ru", time.monotonic() - 1)
    assert res.status == "failed" and res.reason == "timeout"


def test_text_reply_is_nudged_once(parts):
    from app.agent.llm import LLMReply
    writer, llm = make(parts, [LLMReply(text="The answer is 5"), LLMReply(text="still text")])
    res = writer.run("?", "ru", deadline())
    assert res.status == "failed"
    assert llm.chat.sent[1][0] == "text"


def test_llm_error_on_first_send_is_failed(parts):
    from app.agent.llm import LLMError

    class Boom:
        def start_chat(self, system, tools):
            return self

        def send_text(self, text):
            raise LLMError("blocked")

    db, resolver, examples, schema = parts
    res = Writer(Boom(), db, resolver, examples, schema).run("?", "ru", deadline())
    assert res.status == "failed" and res.reason.startswith("llm error")


def test_empty_reply_counts_as_no_tool_call(parts):
    from app.agent.llm import LLMReply
    writer, llm = make(parts, [LLMReply(), call("declare_unanswerable", reason="x")])
    res = writer.run("?", "ru", deadline())
    assert res.status == "unanswerable"
    assert llm.chat.sent[1][0] == "text"


def test_explore_sql_payload_is_json_safe(parts):
    import json
    sql = "SELECT MIN(deal_date) AS d FROM deals"
    writer, llm = make(parts, [call("explore_sql", sql=sql), call("declare_unanswerable", reason="x")])
    writer.run("?", "ru", deadline())
    payload = llm.chat.sent[1][1][0][1]
    assert "error" not in payload, payload
    json.dumps(payload)
    assert isinstance(payload["rows"][0][0], str)


def _err(llm, i=1):
    return llm.chat.sent[i][1][0][1].get("error", "")


@pytest.mark.parametrize("question,flt", [
    ("Сколько сделок в Ташкентской области?", {"regions": ["Ташкент"]}),
    ("Сколько сделок по газетам?", {"products": ["газ"]}),
    ("Сделки брокера BK1234?", {"brokers": ["BK123"]}),
])
def test_review_probes_rejected(parts, question, flt):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2025, **flt}}
    writer, llm = make(parts, [call("compile_query", spec=spec, assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    res = writer.run(question, "ru", deadline())
    assert res.status != "ok" and "resolve" in _err(llm)


def test_contract_id_grounding(parts):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"],
            "filters": {"year": 2025, "contract_id": 999999}}
    writer, llm = make(parts, [call("compile_query", spec=spec, assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    res = writer.run("Сколько сделок по контракту?", "ru", deadline())
    assert res.status != "ok" and "resolve" in _err(llm)
    spec["filters"]["contract_id"] = 102267
    writer, _ = make(parts, [call("compile_query", spec=spec, assumptions=[])])
    res = writer.run("Сколько сделок по контракту 102267?", "ru", deadline())
    assert res.status == "ok"


def test_deal_lookup_id_in_question(parts):
    writer, _ = make(parts, [call("compile_query", spec={"kind": "deal_lookup", "deal_id": 387830}, assumptions=[])])
    assert writer.run("Покажи сделку 387830", "ru", deadline()).status == "ok"
    writer, llm = make(parts, [call("compile_query", spec={"kind": "deal_lookup", "deal_id": 387830},
                                    assumptions=[]), call("declare_unanswerable", reason="x")])
    assert writer.run("Покажи сделку", "ru", deadline()).status == "unanswerable"
    assert "resolve" in _err(llm)


def test_bad_tool_arguments_fed_back(parts):
    writer, llm = make(parts, [call("resolve", kind="nope", text="x"), call("declare_unanswerable", reason="x")])
    res = writer.run("?", "ru", deadline())
    assert res.status == "unanswerable"
    assert "bad arguments" in _err(llm)


def test_two_tool_calls_in_one_reply(parts):
    from app.agent.llm import LLMReply, ToolCall
    both = LLMReply(tool_calls=[ToolCall("resolve", {"kind": "company", "text": "Pudexiz Plast"}),
                                ToolCall("compile_query", {"spec": COUNT_SPEC, "assumptions": []})])
    writer, _ = make(parts, [both])
    # compile_query is evaluated after resolve in the same reply, so the INN is grounded
    assert writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline()).status == "ok"


def test_unanswerable_beats_tool_budget(parts):
    replies = [call("get_examples", question="x")] * 8 + [call("declare_unanswerable", reason="no")]
    writer, _ = make(parts, replies, max_tool_calls=8)
    assert writer.run("?", "ru", deadline()).status == "unanswerable"


def test_sql_splits_filtered_to_axes(parts):
    sql = "SELECT AVG(delivery_term_days) AS avg_days FROM contracts WHERE contract_form = 'Форвард'"
    writer, _ = make(parts, [call("submit_sql", sql=sql, splits=["bogus", "role", "currency", "measure"],
                                  assumptions=[])])
    assert writer.run("Средний срок?", "ru", deadline()).splits == ["role", "currency"]


def _agg(**flt):
    return {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2025, **flt}}


def test_region_exact_case_required(parts):
    writer, llm = make(parts, [call("resolve", kind="region", text="Toshkent viloyati"),
                               call("compile_query", spec=_agg(regions=["TOSHKENT VILOYATI"]), assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    res = writer.run("Сделки в Toshkent viloyati", "ru", deadline())
    assert res.status == "unanswerable" and "resolve" in _err(llm, 2)


def test_free_sql_region_case_rejected(parts):
    sql = "SELECT COUNT(*) AS n FROM deals WHERE region = 'TOSHKENT VILOYATI'"
    writer, llm = make(parts, [call("resolve", kind="region", text="Toshkent viloyati"),
                               call("submit_sql", sql=sql, splits=[], assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    res = writer.run("Сделки в Toshkent viloyati", "ru", deadline())
    assert res.status == "unanswerable" and "resolve" in _err(llm, 2)


def test_product_family_kind_matters(parts):
    fam = "Минеральные удобрения"
    writer, llm = make(parts, [call("resolve", kind="product", text=fam),
                               call("compile_query", spec=_agg(products=[fam]), assumptions=[]),
                               call("compile_query", spec=_agg(product_family=fam), assumptions=[])])
    res = writer.run("Сделки по минеральным удобрениям", "ru", deadline())
    cands = llm.chat.sent[0 + 1][1][0][1]["candidates"]
    assert any(c["kind"] == "product_family" and c["id"] == fam for c in cands), cands
    assert "resolve" in _err(llm, 2)
    assert res.status == "ok" and res.spec.filters.product_family == fam


def test_contract_id_that_is_a_year_rejected(parts):
    writer, llm = make(parts, [call("compile_query", spec=_agg(contract_id=2025), assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    res = writer.run("Сколько сделок в 2025 году?", "ru", deadline())
    assert res.status == "unanswerable" and "resolve" in _err(llm)


# --- final review: ids grounded only by the question must exist ---
def _bk_spec(code):
    return {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"brokers": [code]}}


def test_nonexistent_broker_in_question_rejected(parts):
    writer, llm = make(parts, [call("compile_query", spec=_bk_spec("BK999"), assumptions=[]),
                               call("declare_unanswerable", reason="no such broker")])
    res = writer.run("Сколько сделок заключил брокер BK999?", "ru", deadline())
    assert res.status == "unanswerable"
    err = llm.chat.sent[1][1][0][1]["error"]
    assert "BK999 does not exist in the data; call declare_unanswerable" in err


def test_nonexistent_inn_in_question_rejected(parts):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"seller_inns": ["999999999"]}}
    writer, llm = make(parts, [call("compile_query", spec=spec, assumptions=[]),
                               call("declare_unanswerable", reason="no such inn")])
    res = writer.run("Сколько продал ИНН 999999999?", "ru", deadline())
    assert res.status == "unanswerable"
    assert "999999999 does not exist in the data" in llm.chat.sent[1][1][0][1]["error"]


def test_existing_broker_from_question_accepted(parts):
    writer, _ = make(parts, [call("compile_query", spec=_bk_spec("BK001"), assumptions=[])])
    res = writer.run("Сколько сделок заключил брокер BK001?", "ru", deadline())
    assert res.status == "ok"


def test_existing_contract_from_question_accepted_and_missing_rejected(parts):
    db = parts[0]
    cid = db.query("SELECT MIN(contract_id) FROM contracts").rows[0][0]
    spec = {"kind": "contract_lookup", "contract_id": cid}
    writer, _ = make(parts, [call("compile_query", spec=spec, assumptions=[])])
    assert writer.run(f"Что за контракт {cid}?", "ru", deadline()).status == "ok"
    bad = {"kind": "contract_lookup", "contract_id": 999999}
    writer, llm = make(parts, [call("compile_query", spec=bad, assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    assert writer.run("Что за контракт 999999?", "ru", deadline()).status == "unanswerable"
    assert "does not exist" in llm.chat.sent[1][1][0][1]["error"]


def test_free_sql_nonexistent_inn_literal_rejected(parts):
    sql = "SELECT COUNT(*) FROM deals WHERE seller_inn = '999999999'"
    writer, llm = make(parts, [call("submit_sql", sql=sql, splits=[], assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    res = writer.run("Сколько продал ИНН 999999999?", "ru", deadline())
    assert res.status == "unanswerable"
    assert "999999999 does not exist in the data; call declare_unanswerable" in llm.chat.sent[1][1][0][1]["error"]


def test_resolved_names_exposed(parts):
    writer, _ = make(parts, [call("resolve", kind="company", text="Pudexiz Plast"),
                             call("resolve", kind="product", text="хлопок"),
                             call("compile_query", spec=COUNT_SPEC, assumptions=[])])
    res = writer.run("Сколько продала Pudexiz Plast в 2025?", "ru", deadline())
    assert any("Pudexiz" in n for n in res.resolved_names)
    assert any("хлоп" in n.lower() for n in res.resolved_names)


# --- transient retry in the send path ---
class FlakyChat:
    def __init__(self, fail_times, sleep_s=0.0):
        self.fail_times, self.sleep_s, self.calls = fail_times, sleep_s, 0

    def send_text(self, text):
        self.calls += 1
        if self.calls <= self.fail_times:
            time.sleep(self.sleep_s)
            from google.genai import errors
            raise errors.ServerError(503, {})
        return call("declare_unanswerable", reason="x")


class FlakyLLM:
    def __init__(self, chat):
        self.chat = chat

    def start_chat(self, system, tools):
        return self.chat


def _flaky_writer(parts, chat, timeout):
    db, resolver, examples, schema = parts
    return Writer(FlakyLLM(chat), db, resolver, examples, schema, llm_timeout_s=timeout)


def test_transient_error_retried_once(parts):
    chat = FlakyChat(1)
    res = _flaky_writer(parts, chat, 1.0).run("Q?", "ru", deadline())
    assert res.status == "unanswerable" and chat.calls == 2


def test_transient_error_not_retried_without_time(parts):
    chat = FlakyChat(1, sleep_s=0.35)
    res = _flaky_writer(parts, chat, 0.3).run("Q?", "ru", time.monotonic() + 0.5)
    assert res.status == "failed" and chat.calls == 1


def test_second_transient_error_fails(parts):
    chat = FlakyChat(2)
    res = _flaky_writer(parts, chat, 1.0).run("Q?", "ru", deadline())
    assert res.status == "failed" and chat.calls == 2


def test_exists_cache_bounded_and_not_used_for_inn_broker(parts):
    writer, _ = make(parts, [])
    calls = []
    real = writer.resolver.exists
    writer.resolver.exists = lambda k, v: (calls.append((k, v)), real(k, v))[1]
    for _ in range(2):
        assert writer._exists("inn", "975923536") and writer._exists("broker", "BK001")
    assert len(calls) == 4  # in-memory dicts, no cache
    cid = parts[0].query("SELECT MIN(contract_id) FROM contracts").rows[0][0]
    calls.clear()
    assert writer._exists("contract", str(cid)) and writer._exists("contract", str(cid))
    assert len(calls) == 1
    for i in range(1100):
        writer._exists("deal", str(100000 + i))
    assert len(writer._exists_cache) <= 1024


# --- question/period consistency -------------------------------------------
USD_YEAR = {"kind": "usd_rate", "year": 2024}
USD_MARCH = {"kind": "usd_rate", "year": 2024, "month": 3}
DATE_BAD = {"kind": "deals_aggregate", "measures": ["deal_count"], "status_scope": "split",
            "filters": {"year": 2026}}
DATE_OK = {"kind": "deals_aggregate", "measures": ["deal_count"],
           "filters": {"date_from": "2026-10-30", "date_to": "2026-10-30"}}
DATE_TOPLEVEL = {"kind": "deals_aggregate", "measures": ["deal_count"], "date": "2026-10-30",
                 "status_scope": "split"}


def test_usd_rate_missing_month_fed_back_then_ok(parts):
    writer, llm = make(parts, [call("compile_query", spec=USD_YEAR, assumptions=[]),
                               call("compile_query", spec=USD_MARCH, assumptions=[])])
    res = writer.run("Какой был средний курс доллара в марте 2024 года?", "ru", deadline())
    assert res.status == "ok"
    err = llm.chat.sent[1][1][0][1]["error"]
    assert "month" in err and "usd_rate" in err


def test_toplevel_date_fed_back_then_ok(parts):
    writer, llm = make(parts, [call("compile_query", spec=DATE_TOPLEVEL, assumptions=[]),
                               call("compile_query", spec=DATE_OK, assumptions=[])])
    res = writer.run("Сколько сделок было заключено 30.10.2026?", "ru", deadline())
    assert res.status == "ok"
    assert "filters.date_from" in llm.chat.sent[1][1][0][1]["error"]


def test_date_mentioned_but_only_year_filter(parts):
    writer, llm = make(parts, [call("compile_query", spec=DATE_BAD, assumptions=[]),
                               call("compile_query", spec=DATE_OK, assumptions=[])])
    res = writer.run("Сколько сделок было заключено 30.10.2026?", "ru", deadline())
    assert res.status == "ok"
    err = llm.chat.sent[1][1][0][1]["error"]
    assert "30.10.2026" in err and "date_from" in err


def test_iso_date_mentioned_usd_rate_needs_date(parts):
    writer, llm = make(parts, [call("compile_query", spec=USD_YEAR, assumptions=[]),
                               call("compile_query", spec={"kind": "usd_rate", "date": "2025-06-15"},
                                    assumptions=[])])
    res = writer.run("Kurs 2025-06-15 qancha edi?", "uz", deadline())
    assert res.status == "ok"
    assert "2025-06-15" in llm.chat.sent[1][1][0][1]["error"]


def test_month_question_deals_without_month_rejected(parts):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2024}}
    writer, llm = make(parts, [call("compile_query", spec=spec, assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    writer.run("Сколько сделок в апреле 2024?", "ru", deadline())
    assert "month" in llm.chat.sent[1][1][0][1]["error"]


@pytest.mark.parametrize("q", ["Qancha bitim 2024 yil mart oyida?", "2024 yil birinchi chorak bitimlari",
                               "Сделки за 1 квартал 2024", "Сделки в мае 2024", "Mayda bitim 2024"])
def test_period_words_detected(parts, q):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2024}}
    writer, llm = make(parts, [call("compile_query", spec=spec, assumptions=[]),
                               call("declare_unanswerable", reason="x")])
    writer.run(q, "ru", deadline())
    assert "error" in llm.chat.sent[1][1][0][1]


def test_corrected_month_range_accepted(parts):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"],
            "filters": {"year": 2024, "month_from": 1, "month_to": 3}}
    writer, _ = make(parts, [call("compile_query", spec=spec, assumptions=[])])
    assert writer.run("Сделки за 1 квартал 2024", "ru", deadline()).status == "ok"


@pytest.mark.parametrize("q", ["Сколько сделок было в 2023 году?", "Какая самая популярная команда?",
                               "Сколько сделок по маякам и примерам?", "Фирма Смарт и мартышки в 2023",
                               "Сколько сделок в 2023 году у компании Mayak?"])
def test_no_period_words_unaffected(parts, q):
    spec = {"kind": "deals_aggregate", "measures": ["deal_count"], "filters": {"year": 2023}}
    writer, _ = make(parts, [call("compile_query", spec=spec, assumptions=[])])
    assert writer.run(q, "ru", deadline()).status == "ok"


def test_year_only_usd_ok_without_period(parts):
    writer, _ = make(parts, [call("compile_query", spec=USD_YEAR, assumptions=[])])
    assert writer.run("Средний курс доллара за 2024 год?", "ru", deadline()).status == "ok"


def test_lookup_not_checked(parts):
    writer, _ = make(parts, [call("compile_query", spec={"kind": "deal_lookup", "deal_id": 1},
                                  assumptions=[])])
    res = writer.run("Покажи сделку 1 за март 2024", "ru", deadline())
    assert res.status in ("ok", "failed")
    assert not any("month" in str(t.get("error", "")) for t in res.trace)
