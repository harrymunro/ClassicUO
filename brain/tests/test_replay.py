import argparse
import asyncio
import json

from uo_brain import cli, questions, state
from uo_brain.judge import HeuristicJudge


def test_replay_against_same_judge_agrees(snapshot, tmp_path, capsys):
    sit = state.build(snapshot, set(), [])
    qs = questions.build(sit)
    ans = asyncio.run(HeuristicJudge().ask(sit.state, qs))
    log = tmp_path / "run.jsonl"
    log.write_text(json.dumps({"type": "decision", "state": sit.state, "questions": qs, "answers": ans.to_log()}) + "\n")

    args = argparse.Namespace(log=str(log), judge="heuristic", provider="auto", model=None, limit=10)
    asyncio.run(cli.replay(args))
    out = json.loads(capsys.readouterr().out)
    assert out["decisions"] == 1
    assert out["agreement"] == {"intent": 1.0, "target": 1.0}
