from __future__ import annotations

from jobscout.agent.recovery import recover_tool_calls

NAMES = {"search_postings", "sql_query", "get_posting"}


def test_native_json_in_content() -> None:
    c = ' [{"name":"search_postings","arguments":{"query":"ML roles in Munich"}}]'
    calls = recover_tool_calls(c, NAMES)
    assert [(x["name"], x["args"]) for x in calls] == [
        ("search_postings", {"query": "ML roles in Munich"})
    ]


def test_truncated_json_is_repaired() -> None:
    c = '[{"name":"sql_query","arguments":{"sql":"SELECT count(*) FROM v_postings LIMIT 1"}}'
    assert recover_tool_calls(c, NAMES)[0]["args"]["sql"].endswith("LIMIT 1")


def test_python_call_syntax_in_fence() -> None:
    c = 'I will search.\n```sql_query(sql="SELECT id FROM v_postings LIMIT 5")\n```\nDONE'
    calls = recover_tool_calls(c, NAMES)
    assert calls[0]["name"] == "sql_query" and calls[0]["args"] == {
        "sql": "SELECT id FROM v_postings LIMIT 5"
    }


def test_unknown_tools_and_plain_text_ignored() -> None:
    assert recover_tool_calls('[{"name":"drop_db","arguments":{}}]', NAMES) == []
    assert recover_tool_calls("DONE", NAMES) == []
    assert recover_tool_calls("search_postings(__import__('os'))", NAMES) == []
    assert recover_tool_calls("search_postings(query=open('x').read())", NAMES) == []
    assert recover_tool_calls(
        '{"function": {"name": "get_posting", "arguments": "{\\"posting_id\\": \\"an-0000000001\\"}"}}',
        NAMES,
    )[0]["args"] == {"posting_id": "an-0000000001"}


def test_name_then_json() -> None:
    c = 'Call: search_postings {"query": "rag", "filters": {"city": "Berlin"}}'
    assert recover_tool_calls(c, NAMES)[0]["args"] == {
        "query": "rag",
        "filters": {"city": "Berlin"},
    }


def test_missing_inner_brace_is_balanced() -> None:
    c = ' [{"name": "search_postings", "arguments": {"query": "machine learning RAG", "filters": {"city": "München", "job_family": "data_ml"}}]'
    calls = recover_tool_calls(c, NAMES)
    assert calls[0]["args"]["filters"] == {"city": "München", "job_family": "data_ml"}


def test_balance_json_keeps_braces_inside_strings() -> None:
    from jobscout.agent.recovery import balance_json

    assert balance_json('{"a": "x}]"') == '{"a": "x}]"}'
    assert balance_json('[{"a": 1]') == '[{"a": 1}]'
