package p;

public class FieldAnon {
    static int make() { return 1; }
    interface Maker { int build(); }
    static abstract class Base { abstract void go(); void shared() {} }
    final Maker maker = new Maker() {
        public int build() {
            return make();
        }
    };
    static final Base BASE = new Base() {
        int n = make();
        void go() {
            shared();
            make();
        }
    };
    void use() {
        maker.build();
        BASE.go();
    }
}
