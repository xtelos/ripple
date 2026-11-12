package p;

class Work { static void go() {} }
class A1 { public void run() { Work.go(); } }
class Task1 extends A1 implements Runnable {}

class A2 { public void run() { Work.go(); } }
class B2 extends A2 {}
class Task2 extends B2 implements Runnable {}

class A3 { public void run() { Work.go(); } }
class B3 extends A3 {}
class C3 extends B3 {}
class Task3 extends C3 implements Runnable {}

public class Levels {
    void h() {
        Runnable r1 = new Task1();
        Runnable r2 = new Task2();
        Runnable r3 = new Task3();
        r1.run();
        r2.run();
        r3.run();
        Runnable plain = () -> {};
        plain.run();
    }
}
