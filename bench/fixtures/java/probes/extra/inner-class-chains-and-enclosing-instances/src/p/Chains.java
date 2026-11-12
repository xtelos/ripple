package p;

public class Chains {
    class A {
        class B {
            void leaf() {}
        }
        B make() { return new B(); }
    }
    A a = new A();
    void use(Chains other) {
        a.make().leaf();
        a.new B().leaf();
        other.a.make().leaf();
        new Chains().new A().make();
    }
}
