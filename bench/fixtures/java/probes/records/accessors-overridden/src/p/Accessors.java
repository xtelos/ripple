package p;

interface Named { String name(); }
record Person(String name, int age) implements Named {
    public String name() { return name.trim(); }
}
record Plain(String name) implements Named {}
class Other { String name() { return "o"; } int age() { return 1; } }

public class Accessors {
    Other age = new Other();
    void h(Person p, Plain q, Named n) {
        p.name();
        p.age();
        q.name();
        n.name();
        age.age();
    }
}
