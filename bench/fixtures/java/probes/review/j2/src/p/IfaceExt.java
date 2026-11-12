package p;

import java.util.function.Supplier;

interface Src extends Supplier<String> {
    String get(int n);
}

class SrcImpl implements Src {
    public String get() {
        return "";
    }

    public String get(int n) {
        return "";
    }
}

class IfaceExt {
    <T extends Src> String f(T t, SrcImpl s, Src i) {
        return t.get(1) + s.get(1) + i.get(1) + s.get();
    }
}
