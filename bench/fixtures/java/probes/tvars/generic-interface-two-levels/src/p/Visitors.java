package p;

class Item {}
interface Visitor<T> { void visit(T x); }
abstract class BaseV<T> implements Visitor<T> {
    public void visit(T x) { helper(x); }
    void helper(T x) {}
}
class ItemV extends BaseV<Item> {
    public void visit(Item x) {}
    public void visit(String s) {}
    public void visit(Item x, int n) {}
}
class StrV extends BaseV<String> {
    public void visit(String s) {}
    public void visit(Item x) {}
}

public class Visitors {
    void h(Visitor<Item> v, BaseV<String> b, ItemV iv) {
        v.visit(new Item());
        b.visit("s");
        iv.visit("s");
        iv.visit(new Item());
        new StrV().visit("t");
    }
}
