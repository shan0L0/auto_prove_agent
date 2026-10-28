import json

from agent.logging_utils import RunLogger


def test_run_logger_writes_llm_trace_files(tmp_path):
    logger = RunLogger(root=tmp_path)

    logger.llm_interaction(
        "propose_round_1",
        {
            "mode": "structured",
            "candidate_count": 1,
            "error": "",
            "request_messages": [{"role": "human", "content": "prove this"}],
            "response_text": '{"candidates":[]}',
        },
    )

    jsonl = logger.llm_trace_path.read_text(encoding="utf-8").strip()
    markdown = logger.llm_markdown_path.read_text(encoding="utf-8")
    record = json.loads(jsonl)

    assert record["phase"] == "propose_round_1"
    assert record["request_messages"][0]["content"] == "prove this"
    assert "### Prompt Messages" in markdown
    assert '{"candidates":[]}' in markdown
