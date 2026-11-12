package p;

public class Single {
    private Single(String s) {}
    Single(Object o) {}
    static class Maker {
        Single make() {
            return new Single("x");
        }
    }
}
