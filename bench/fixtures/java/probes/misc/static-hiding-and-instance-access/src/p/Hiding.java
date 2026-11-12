package p;

public class Hiding {
    static class Base {
        static void sm() {}
        void im() {}
    }
    static class Sub extends Base {
        static void sm() {}
        void im() {}
    }
    void use(Base b, Sub s) {
        b.sm();
        s.sm();
        Base.sm();
        Sub.sm();
        b.im();
        s.im();
    }
}
