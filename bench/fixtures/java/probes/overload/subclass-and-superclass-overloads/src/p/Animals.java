package p;

public class Animals {
    static class Animal {}
    static class Dog extends Animal {}
    static class Cat extends Animal {}
    void f(Animal a) {}
    void f(Dog d) {}
    void g(Animal a) {}
    void use(Animal a, Dog d, Cat c) {
        f(a);
        f(d);
        f(c);
        f(new Dog());
        f(new Animal());
        g(d);
        g(new Cat());
    }
}
