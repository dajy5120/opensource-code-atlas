from osca.resolve import place

OURS = """/// Adds.
/// 【zh】 加法
pub fn add(a: i32, b: i32) -> i32 {
    // 【zh】 直接相加
    a + b
}

// 【zh】 将被删除
pub fn gone() {}
"""

THEIRS = """use std::ops::Add;

/// Adds two values.
pub fn add(a: i32, b: i32) -> i32 {
    let c = 0;
    a + b + c
}
"""


def test_place_reattaches_relocates_and_orphans():
    merged, out = place("x.rs", OURS, THEIRS, "【zh】")
    assert merged == """use std::ops::Add;

/// Adds two values.
/// 【zh】 加法
pub fn add(a: i32, b: i32) -> i32 {
    // 【zh】 直接相加
    let c = 0;
    a + b + c
}
"""
    assert out.reattached == 2
    assert [o["symbol"] for o in out.orphaned] == ["x.rs#gone"]
