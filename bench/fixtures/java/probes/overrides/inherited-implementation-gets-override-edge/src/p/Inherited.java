package p;

interface I { void m(); }
class A { public void m() {} }
class B extends A implements I {}
class C extends B {}
class D implements I { public void m() {} }
interface J { default void m() {} }
class E implements J {}

public class Inherited {
    void h(I i, J j) {
        i.m();
        j.m();
        new B().m();
        new C().m();
    }
}
