from osca import state
from osca.symbols import index_text

V1 = "/// Adds.\n// 【zh】 加法\npub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"


def syms(text):
    return state.all_symbols({"x.rs": index_text("x.rs", text)})


def status_of(records, text, name="add"):
    s = syms(text)[f"x.rs#{name}"]
    return state.derive(s, records.get(s.id))


def test_lifecycle():
    records = {}
    assert status_of(records, V1).status == "translated"  # no record yet
    state.update(records, syms(V1), "me")
    assert status_of(records, V1).status == "translated"

    body = V1.replace("a + b", "a.wrapping_add(b)")
    d = status_of(records, body)
    assert (d.status, d.changed, d.priority) == ("stale", ["body"], "P2")
    sig = V1.replace("b: i32)", "b: i64)")
    assert status_of(records, sig).priority == "P0"

    # update() does NOT refresh a stale record whose annotation is unchanged
    state.update(records, syms(body), "me")
    assert status_of(records, body).status == "stale"
    # editing the annotation = re-translation
    edited = body.replace("加法", "回绕加法")
    state.update(records, syms(edited), "me")
    assert status_of(records, edited).status == "translated"

    state.approve(records, [syms(edited)["x.rs#add"]], "reviewer")
    assert status_of(records, edited).status == "reviewed"
    assert status_of(records, edited.replace("wrapping_add", "saturating_add")).status == "stale"


def test_rename_migrates_record():
    records = {}
    state.update(records, syms(V1), "me")
    renamed = V1.replace("fn add", "fn plus")
    changes = state.update(records, syms(renamed), "me")
    assert changes["migrated"] == ["x.rs#add -> x.rs#plus"]
    d = status_of(records, renamed, "plus")
    assert d.status == "stale" and d.changed == ["sig"]


def test_removed_annotation_drops_record():
    records = {}
    state.update(records, syms(V1), "me")
    state.update(records, syms(V1.replace("// 【zh】 加法\n", "")), "me")
    assert records == {}
