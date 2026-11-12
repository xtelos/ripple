package p;

public class Colors {
    static int make() { return 1; }
    enum Color {
        RED(make()), GREEN(2);
        final int v;
        Color(int v) { this.v = v; }
        int get() { return v; }
    }
    int f(Color c) {
        switch (c) {
            case RED:
                return c.get();
            default:
                return Color.GREEN.get();
        }
    }
}
