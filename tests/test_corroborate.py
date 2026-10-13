"""
Offline tests for contracts/corroborate.py.

Run:  python3 tests/test_corroborate.py

tests/mock_genlayer.py is a small SDK stand-in: it proves the deterministic
logic and the validator rules. Live behaviour must also be checked on Bradbury.
"""

import importlib.util
import json
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mock_genlayer as mg  # noqa: E402

mg.install()

CONTRACT = HERE.parent / "contracts" / "corroborate.py"
GEN = 10**18
A, B, C, D = ("https://en.wikipedia.org/wiki/Nigeria", "https://www.britannica.com/place/Nigeria",
              "https://www.cia.gov/the-world-factbook/countries/nigeria", "https://www.bbc.com/news/nigeria")
CLAIM = "Abuja is the capital of Nigeria"
ALICE = mg.Address("0x" + "a" * 40)
BOB = mg.Address("0x" + "b" * 40)


def load():
    spec = importlib.util.spec_from_file_location("corroborate", CONTRACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.datetime = mg._FrozenDatetime
    return mod


def fresh():
    mg.world = mg.World()
    vars(mg)["world"] = mg.world
    mod = load()
    c = mod.Corroborate()
    c.__init__()
    for u, t in ((A, "page A"), (B, "page B"), (C, "page C"), (D, "page D")):
        mg.world.set_web(u, t)
    return mod, c


def as_(addr, value=0):
    mg.world.sender, mg.world.value = addr, value


def llm(stances, role=None):
    mg.world.set_llm(lambda p: {"stances": list(stances), "note": "checked"}, role)


def submit(c, stances, sources=(A, B)):
    llm(stances)
    as_(ALICE)
    return json.loads(c.submit_claim(CLAIM, "\n".join(sources)))


def expect_error(fn, contains):
    try:
        fn()
    except (mg.UserError, mg.ConsensusFailure) as e:
        msg = getattr(e, "message", str(e))
        assert contains in msg, f"expected {contains!r} in {msg!r}"
        return
    raise AssertionError(f"expected error containing {contains!r}")


def test_runner_header_and_size():
    lines = CONTRACT.read_text().splitlines()
    assert lines[0].startswith('# { "Depends": "py-genlayer:')
    assert not lines[1].startswith("#")
    src = CONTRACT.read_text()
    assert "web.get" not in src and "strict_eq" not in src and "@dataclass" not in src
    assert len(src.encode()) < 18000


def test_aggregate_rules():
    mod = load()
    S, X, U = mod.SUPPORTS, mod.CONTRADICTS, mod.UNCLEAR
    assert mod.aggregate([S, S]) == "TRUE"
    assert mod.aggregate([X, X, U]) == "FALSE"
    assert mod.aggregate([S, S, X]) == "DISPUTED"
    assert mod.aggregate([S, U]) == "UNVERIFIED"
    assert mod.aggregate([U, U, U]) == "UNVERIFIED"


def test_sources_must_be_distinct_https_domains():
    mod, c = fresh()
    llm(["SUPPORTS", "SUPPORTS"]); as_(ALICE)
    expect_error(lambda: c.submit_claim(CLAIM, f"{A}\nhttps://en.wikipedia.org/wiki/Abuja"), "different domains")
    expect_error(lambda: c.submit_claim(CLAIM, f"{B}\nhttps://britannica.com/x"), "different domains")  # www. ignored
    expect_error(lambda: c.submit_claim(CLAIM, f"{A}\nhttp://example.com"), "https://")
    expect_error(lambda: c.submit_claim(CLAIM, A), "2 to 3 sources")
    expect_error(lambda: c.submit_claim("short", f"{A}\n{B}"), "characters")


def test_two_supporting_sources_make_true():
    mod, c = fresh()
    out = submit(c, ["SUPPORTS", "SUPPORTS"])
    assert out == {"id": 0, "verdict": "TRUE", "stances": ["SUPPORTS", "SUPPORTS"]}
    assert c.get_verdict(0, 0) == "TRUE"
    d = json.loads(c.get_claim(0))
    assert d["sources"] == [A, B] and d["author"] == ALICE.as_hex.lower()


def test_conflicting_sources_are_disputed():
    mod, c = fresh()
    assert submit(c, ["SUPPORTS", "CONTRADICTS", "SUPPORTS"], (A, B, C))["verdict"] == "DISPUTED"


def test_validators_compare_the_verdict_not_each_stance():
    mod, c = fresh()
    # leader and validators word one source differently, but both conclusions are TRUE
    llm(["SUPPORTS", "SUPPORTS", "UNCLEAR"], "leader")
    llm(["SUPPORTS", "SUPPORTS", "SUPPORTS"], "validator")
    as_(ALICE)
    assert json.loads(c.submit_claim(CLAIM, f"{A}\n{B}\n{C}"))["verdict"] == "TRUE"


def test_leader_hiding_a_contradiction_is_rejected():
    mod, c = fresh()
    # truth is DISPUTED; a leader that calls the contradicting source UNCLEAR would make it TRUE
    llm(["SUPPORTS", "SUPPORTS", "UNCLEAR"], "leader")
    llm(["SUPPORTS", "SUPPORTS", "CONTRADICTS"], "validator")
    as_(ALICE)
    expect_error(lambda: c.submit_claim(CLAIM, f"{A}\n{B}\n{C}"), "validators agreed")
    assert c.get_claim_count() == 0


def test_unloadable_source_counts_as_unclear():
    mod, c = fresh()
    mg.world.web["leader"].pop(B); mg.world.web["validator"].pop(B)
    mg.world.set_llm(lambda p: {"stances": ["SUPPORTS"] * p.count("<<<SOURCE"), "note": "x"})
    as_(ALICE)
    out = json.loads(c.submit_claim(CLAIM, f"{A}\n{B}"))
    assert out["stances"] == ["SUPPORTS", "UNCLEAR"] and out["verdict"] == "UNVERIFIED"


def test_no_source_loads_is_a_transient_error():
    mod, c = fresh()
    llm(["SUPPORTS", "SUPPORTS"]); as_(ALICE)
    expect_error(lambda: c.submit_claim(CLAIM, "https://a.example/x\nhttps://b.example/y"), "TRANSIENT")


def test_wrong_stance_count_is_rejected():
    mod, c = fresh()
    llm(["SUPPORTS"]); as_(ALICE)
    expect_error(lambda: c.submit_claim(CLAIM, f"{A}\n{B}"), "validators agreed")


def test_challenge_that_changes_the_verdict_refunds_the_bond():
    mod, c = fresh()
    submit(c, ["SUPPORTS", "SUPPORTS"])
    llm(["CONTRADICTS"]); as_(BOB, GEN // 10)
    res = json.loads(c.challenge(0, C))
    assert res == {"changed": True, "before": "TRUE", "verdict": "DISPUTED"}
    assert mg.world.transfers == [(BOB.as_hex.lower(), GEN // 10)]
    d = json.loads(c.get_claim(0))
    assert d["sources"] == [A, B, C] and d["challenges"] == 1 and d["flips"] == 1


def test_failed_challenge_pays_the_author():
    mod, c = fresh()
    submit(c, ["SUPPORTS", "SUPPORTS"])
    llm(["SUPPORTS"]); as_(BOB, GEN // 10)
    res = json.loads(c.challenge(0, C))
    assert not res["changed"] and res["verdict"] == "TRUE"
    assert mg.world.transfers == [(ALICE.as_hex.lower(), GEN // 10)]


def test_challenge_rules():
    mod, c = fresh()
    submit(c, ["SUPPORTS", "SUPPORTS"])
    llm(["SUPPORTS"])
    as_(BOB, 0)
    expect_error(lambda: c.challenge(0, C), "0.1 GEN")
    as_(BOB, GEN // 10)
    expect_error(lambda: c.challenge(0, "https://en.wikipedia.org/wiki/Abuja"), "different domains")
    expect_error(lambda: c.challenge(0, f"{C}\n{D}"), "exactly one")
    expect_error(lambda: c.challenge(7, C), "unknown claim")


def test_refresh_waits_then_rechecks():
    mod, c = fresh()
    submit(c, ["SUPPORTS", "SUPPORTS"])
    llm(["CONTRADICTS", "CONTRADICTS"])
    expect_error(lambda: c.refresh(0), "refresh is allowed after")
    mg.world.now += 7 * 3600
    res = json.loads(c.refresh(0))
    assert res == {"changed": True, "before": "TRUE", "verdict": "FALSE"}
    assert json.loads(c.get_claim(0))["flips"] == 1


def test_get_verdict_reports_stale():
    mod, c = fresh()
    submit(c, ["SUPPORTS", "SUPPORTS"])
    assert c.get_verdict(0, 3600) == "TRUE"
    mg.world.now += 7200
    assert c.get_verdict(0, 3600) == "STALE"
    assert c.get_verdict(0, 0) == "TRUE"


def test_render_settings():
    mod, c = fresh()
    submit(c, ["SUPPORTS", "SUPPORTS"])
    r = [x for x in mg.world.nondet_calls if x[0] == "render"]
    assert r and all(x[3] == "text" and x[4] == "1s" for x in r)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"PASS  {name}")
        except Exception:
            failed += 1; print(f"FAIL  {name}"); traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
