package p.a;

public class A {
    void m() {}
    protected void pm() {}
    public void run(A x) { x.m(); x.pm(); }
}
