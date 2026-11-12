package p;

public class Ctor {
    static class Base {
        Base() {
            init();
        }
        void init() {}
    }
    static class Sub extends Base {
        void init() {}
    }
    static class Sub2 extends Sub {
        Sub2() {
            super();
            init();
        }
    }
    void use() {
        new Sub();
        new Sub2();
    }
}
