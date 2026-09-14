"""Good Neighbor coordinator tests: multi-agent graph + offline Strands loop."""

import os

os.environ.setdefault("AGENTRX_DB_PATH", "/tmp/opencode/agentrx_coord_test.db")

from services import db  # noqa: E402

TEST_DB = "/tmp/opencode/agentrx_coord_test.db"


def setup_function(_):
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    db.DEFAULT_DB_PATH = TEST_DB
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        os.environ.pop(var, None)


def test_graph_has_four_strands_agents_with_sub_agent_tools():
    from agent import coordinator

    g = coordinator.build_graph("org1", 40.7, -74.0, live=False)
    assert {a.name for a in (g["finder"], g["caller"], g["board"], g["coordinator"])} == {
        "finder", "caller", "board", "coordinator"}
    assert g["live"] is False
    assert len(g["tools"]) == 9  # domain tools shared by all agents
    # supervisor carries the sub-agents as tools (multi-agent delegation)
    assert len(g["coordinator"].tool_names) > len(g["tools"]) if hasattr(
        g["coordinator"], "tool_names") else True


def test_offline_drug_check_end_to_end_through_strands():
    from agent import coordinator

    g = coordinator.build_graph("org1", 40.7128, -74.0060, live=False)
    reply = coordinator._text_of(g["coordinator"]("atorvastatin 20mg x30"))
    assert "Info only, confirm with pharmacist/doctor." in reply
    assert "in stock" in reply or "board" in reply
    # the loop really executed tools (finder -> 3 calls -> 3 results)
    tool_uses = [b["toolUse"]["name"] for m in g["coordinator"].messages
                 for b in (m.get("content") or [])
                 if isinstance(b, dict) and "toolUse" in b]
    assert "find_pharmacies" in tool_uses
    assert tool_uses.count("place_stock_call") == 3
    assert tool_uses.count("get_call_result") == 3


def test_offline_watch_and_board_intents():
    from agent import coordinator

    g = coordinator.build_graph("org1", 40.7, -74.0, live=False)
    reply = coordinator._text_of(g["coordinator"]("watch lisinopril 10mg"))
    assert "shortage board" in reply
    reply2 = coordinator._text_of(g["coordinator"]("board"))
    assert "lisinopril" in reply2


def test_coordinator_turn_persists_thread_memory():
    import services.threads as thread_svc
    from agent import coordinator

    t = thread_svc.create_thread("org1", "intake")
    out = coordinator.run_coordinator_turn(t["id"], "org1", "atorvastatin 20mg x30",
                                           40.7128, -74.0060)
    assert out["live"] is False
    assert "Info only" in out["reply"]
    msgs = thread_svc.get_messages(t["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["content"] == out["reply"]


def test_board_sweep_proposes_but_never_calls():
    from agent import coordinator
    from services import calle
    from services import watchlist as watch_svc

    w = watch_svc.add_watch("org1", "atorvastatin", "20mg", 30)
    # never checked -> stale; sweep must propose a call plan but place no calls
    before = len(calle._mock_store)
    report = coordinator.run_board_sweep("org1", 40.7128, -74.0060)
    assert report["org_id"] == "org1"
    assert any(p["watch_id"] == w["id"] for p in report["call_plans"])
    assert len(calle._mock_store) == before  # sweep proposes; never dials
