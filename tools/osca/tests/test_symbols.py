from osca.markers import strip_text
from osca.symbols import MODULE, index_text

RUST = '''//! Module doc.
//! 【zh】 模块说明

/// A book.
/// 【zh】 订单簿
#[derive(Debug)]
pub struct Book {
    // 【zh】 买盘
    bids: Vec<u8>,
}

// 【zh】 实现块
impl Book {
    /// New.
    pub fn new() -> Self {
        // 【zh】 创建空簿
        Self { bids: vec![] }
    }

    fn helper(&self) {}
}

#[cfg(test)]
mod tests {
    fn t() {}
}
'''


def ids(idx):
    return {s.qualname: s for s in idx.symbols.values()}


def test_rust_symbols_and_attachment():
    idx = index_text("src/book.rs", RUST)
    s = ids(idx)
    assert set(s) == {MODULE, "Book", "impl Book", "impl Book::new", "impl Book::helper"}  # tests skipped
    assert len(s[MODULE].zh_rows) == 1
    assert len(s["Book"].zh_rows) == 2  # doc-append + field comment
    assert len(s["impl Book"].zh_rows) == 1
    assert len(s["impl Book::new"].zh_rows) == 1
    assert s["impl Book::helper"].zh is None
    assert s["Book"].translatable and not s["impl Book"].translatable
    assert not s["impl Book::helper"].translatable  # no doc, tiny body


def test_hashes_ignore_annotations_and_formatting():
    rs = ("//", "///", "//!")
    stripped, _ = strip_text(RUST, rs)
    a, b = ids(index_text("x.rs", RUST)), ids(index_text("x.rs", stripped))
    assert all(a[k].hashes == b[k].hashes for k in a)
    reformatted = stripped.replace("Self { bids: vec![] }", "Self {\n            bids: vec![],\n        }".replace(",\n        }", "\n        }"))
    c = ids(index_text("x.rs", reformatted))
    assert c["impl Book::new"].body == a["impl Book::new"].body


def test_container_body_ignores_child_changes():
    changed = RUST.replace("Self { bids: vec![] }", "Self { bids: Vec::new() }")
    a, b = ids(index_text("x.rs", RUST)), ids(index_text("x.rs", changed))
    assert a["impl Book::new"].body != b["impl Book::new"].body
    assert a["impl Book"].body == b["impl Book"].body
    doc_changed = RUST.replace("/// New.", "/// Creates a book.")
    c = ids(index_text("x.rs", doc_changed))
    assert (c["impl Book::new"].doc, c["impl Book::new"].sig) != (a["impl Book::new"].doc, a["impl Book::new"].sig)
    assert c["impl Book::new"].body == a["impl Book::new"].body


PY = '''"""Mod doc."""


# 【zh】 风险引擎
class Engine:
    """Doc."""

    limit = 3

    # 【zh】 检查订单
    @staticmethod
    def check(order):
        """Check."""
        # 【zh】 先看数量
        return order.qty < 10
'''


def test_python_symbols():
    s = ids(index_text("pkg/engine.py", PY))
    assert set(s) == {MODULE, "Engine", "Engine.check"}
    assert len(s["Engine"].zh_rows) == 1 and len(s["Engine.check"].zh_rows) == 2
    assert s["Engine.check"].kind == "method"


PYX = '''# 【zh】 风险引擎
cdef class RiskEngine(Component):
    """Engine doc
    spanning lines
"""

    def __init__(self, int x):
        self.x = x

    cpdef void check(
        self,
        Order order,
    ):
        # 【zh】 检查
        if order is None:
            return

cdef inline double _price(double p):
    return p
'''


def test_cython_symbols():
    s = ids(index_text("nautilus/risk/engine.pyx", PYX))
    assert set(s) == {MODULE, "RiskEngine", "RiskEngine.__init__", "RiskEngine.check", "_price"}
    assert s["RiskEngine.check"].kind == "method"
    assert len(s["RiskEngine"].zh_rows) == 1 and len(s["RiskEngine.check"].zh_rows) == 1
    assert s["_price"].start == s["_price"].item_row
